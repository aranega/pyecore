import typing
import pyecore.ecore as Ecore
from pyecore.resources import ResourceSet, URI, Resource
import functools
from pyecore.notification import EObserver
import inspect
from . import TransformationTrace as trace


class ResultObserver(EObserver):
    def notifyChanged(self, notif):
        print(notif)


class EObjectProxy(object):
    def __init__(self, instance):
        object.__setattr__(self, 'wrapped', instance)
        object.__setattr__(self, 'wrapped_eClass', instance.eClass)

    def __getattribute__(self, name):
        wrapped = object.__getattribute__(self, 'wrapped')
        eClass = object.__getattribute__(self, 'wrapped_eClass')
        result = getattr(wrapped, name)
        if eClass.findEStructuralFeature(name):
            print('access', name, ':', result, 'for', wrapped)
        return result

    def __eq__(self, other):
        return object.__getattribute__(self, 'wrapped').__eq__(other)

    def __hash__(self):
        return object.__getattribute__(self, 'wrapped').__hash__()

    def __setattr__(self, name, value):
        wrapped = object.__getattribute__(self, 'wrapped')
        if isinstance(value, EObjectProxy):
            value = object.__getattribute__(value, 'wrapped')
        return setattr(wrapped, name, value)

    def __str__(self):
        wrapped = object.__getattribute__(self, 'wrapped')
        return wrapped.__str__()


def objects(resource):
    for elt in resource.contents:
        yield elt
        yield from elt.eAllContents()


def objects_of_kind(resource, type):
    for elt in resource.contents:
        if isinstance(elt, type):
            yield elt
        for x in elt.eAllContents():
            if isinstance(x, type):
                yield x


class Parameters(object):
    def __init__(self, transformation, parameter_names):
        self.transformation = transformation
        self.parameter_names = parameter_names

    def __getitem__(self, item):
        if type(item) is str:
            return getattr(self, item)
        return getattr(self, self.parameter_names[item])


def load_model(model_path):
    rset = ResourceSet()
    resource = rset.get_resource(model_path)
    return resource


class Transformation(object):
    def __init__(self, name, inputs, outputs):
        self.name = name
        self.inputs_def = inputs if inputs else []
        self.outputs_def = outputs if outputs else []
        self.registed_mapping = []
        self._main = None

    @property
    def inouts(self):
        return [k for k in self.inputs_def if k in self.outputs_def]

    def main(self, fun):
        self._main = fun
        return fun

    def run(self, clean_mappings_cache=True, resource_set=None, **kwargs):
        sp = inspect.currentframe()
        context = TransformationExecution(self, resource_set)
        sp.f_globals["mycontext"] = context

        params = {}
        for in_model in self.inputs_def:
            try:
                param = kwargs.pop(in_model)
                if isinstance(param, Ecore.EObject):
                    if param.eResource:
                        resource = param.eResource
                    else:
                        rset = context.resource_set
                        resource = rset.create_resource(URI(in_model))
                        resource.append(param)
                elif isinstance(param, Resource):
                    resource = param
                else:
                    resource = load_model(param)
                setattr(context.inputs, in_model, resource)
                params[in_model] = resource
                if in_model in self.inouts:
                    setattr(context.outputs, in_model, resource)
                    params[in_model] = resource
            except KeyError as e:
                raise type(e)(str(e) + ' is a missing input model'
                              .format(in_model)) from None
        for out_model in list(set(self.outputs_def) - set(self.inouts)):
            resource = context.resource_set.create_resource(URI(out_model))
            setattr(context.outputs, out_model, resource)
            params[out_model] = resource
        context.primary_output = context.outputs[0]
        self._main(**params)
        if clean_mappings_cache:
            for mapping in self.registed_mapping:
                mapping.cache.cache_clear()
        return context

    def mapping(self, f=None, output_model=None, when=None):
        if not f:
            return functools.partial(self.mapping,
                                     output_model=output_model,
                                     when=when)

        self.registed_mapping.append(f)
        f.__mapping__ = True
        result_var_name = 'result'
        self_var_name = 'self'
        f.self_eclass = typing.get_type_hints(f).get(self_var_name)
        if f.self_eclass is None:
            raise ValueError("Missing 'self' parameter for mapping: '{}'"
                             .format(f.__name__))
        f.result_eclass = typing.get_type_hints(f).get('return')
        f.inout = f.result_eclass is None
        output_model_name = output_model or self.outputs_def[0]
        f.output_def = None if f.inout else output_model_name

        @functools.wraps(f)
        def inner(*args, **kwargs):
            if f.inout:
                index = f.__code__.co_varnames.index(self_var_name)
                result = kwargs.get(self_var_name, args[index])
            elif f.result_eclass is Ecore.EClass:
                result = f.result_eclass('')
            else:
                result = f.result_eclass()
            inputs = [a for a in args if isinstance(a, Ecore.EObject)]
            print('CREATE', result, 'FROM', inputs, 'BY', f.__name__)

            # Create object for the trace
            sp = inspect.currentframe()
            context = sp.f_globals["mycontext"]
            # try:
            #     rule = context.trace[f.__name__]
            # except Exception:
            #     rule = trace.Rule(transformation=context.trace, name=f.__name__)
            # context.trace.rules.append(rule)
            # record = trace.Record()
            # for element in args:
            #     if isinstance(element, Ecore.EObject):
            #         record.inputs.append(trace.Attribute(old_value=element))
            #     else:
            #         record.inputs.append(trace.ObjectReference(element))
            # record.outputs.append(trace.ObjectReference(old_value=result))
            # rule.records.append(record)

            # Inject new parameter
            g = f.__globals__
            marker = object()
            oldvalue = g.get(result_var_name, marker)
            g[result_var_name] = result
            observer = ResultObserver(notifier=result)
            new_args = [EObjectProxy(obj)
                        if isinstance(obj, Ecore.EObject)
                        else obj
                        for obj in args]

            for key, value in kwargs.items():
                if isinstance(value, Ecore.EObject):
                    kwargs[key] = EObjectProxy(value)
            try:
                f(*new_args, **kwargs)
            finally:
                if oldvalue is marker:
                    del g[result_var_name]
                else:
                    g[result_var_name] = oldvalue
                result.listeners.remove(observer)
                if f.output_def and \
                        result not in context.outputs[f.output_def].contents:
                    context.outputs[f.output_def].append(result)
            return result
        if when:
            @functools.wraps(inner)
            def when_inner(*args, **kwargs):
                if when(*args, **kwargs):
                    return inner(*args, **kwargs)
                return when_inner
        cached_fun = functools.lru_cache()(inner)
        f.cache = cached_fun
        return cached_fun

    def disjunct(self, f=None, mappings=None):
        if not f:
            return functools.partial(self.disjunct, mappings=mappings)

        @functools.wraps(f)
        def inner(*args, **kwargs):
            for fun in mappings:
                result = fun(*args, **kwargs)
                if result is not None:
                    break
            f(*args, **kwargs)
            return result
        return inner


class TransformationExecution(object):
    def __init__(self, transfo, resource_set=None):
        # self.trace = trace.TransformationTrace()
        self.trace = None  # not yet supported
        self.inputs = Parameters(transfo, transfo.inputs_def)
        self.outputs = Parameters(transfo, transfo.outputs_def)
        self.transformation = transfo
        self.resource_set = resource_set if resource_set else ResourceSet()
