from pyecore.resources.xmi import XMIResource
from .transfo_example import ghmde2graph as transfo

# generated using
# https://github.com/kolovos/datasets/blob/master/github-mde/ghmde.ecore
# as input metamodel
import ghmde


# quick model def
a = ghmde.Repository(name='repo')
f = ghmde.File(path='test')
a.files.append(f)

# b = ghmde.Repository(name='repo2')

resource = XMIResource()
resource.append(a)
# resource.append(b)

# run transfo (multi-root)
result = transfo.run(ghmde_model=resource)

print(result.inputs.ghmde_model.contents[0])
print(result.outputs.graph_model.contents)
print(result.outputs.graph_model.contents[0].nodes)

# run transfo (single direct root)
transfo.run(ghmde_model=a)

print(result.inputs.ghmde_model.contents[0])
print(result.outputs.graph_model.contents)
print(result.outputs.graph_model.contents[0].nodes)
