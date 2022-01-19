"""
Subworkflows
------------

Launch plans were introduced in the Basics section of this book. Subworkflows are similar in that they allow users
to kick off one workflow from inside another. What's the difference? Think of launch plans as passing by pointer and
subworkflows as passing by value.

.. note::

    the real reason why subworkflows exist is because this is exactly how dynamic workflows are handled by flyte. So
    instead of hiding the functionality, we expose the functionality at the user level. There are pros and cons of
    using subworkflows as described below

When you include a launch plan of workflow A inside workflow B, when B gets run, a new workflow execution,
replete with a new workflow execution ID, a new Flyte UI link, will be run.

When you include workflow A as a subworkflow of workflow B, when B gets run, the entire workflow A graph is basically
copied into workflow B at the point where it is called.

When should I use SubWorkflows?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
If you want to limit parallelism within a workflow and its launched subworkflows, Subworkflows provide a clean way
to do that. This is because they execute within the same context of the parent workflow. Thus all nodes of a subworkflow
will be constrained to the total constraint on the parent workflow

When you use LaunchPlans within a workflow to launch an execution of a previously defined workflow, a new
external execution is launched, with a separate execution ID and can be observed as a distinct entity in
FlyteConsole/Flytectl etc. Moreover, the context is not shared, hence they may have separate parallelism constraints.
We refer to these externalized invocations of a workflow using Launchplans from a parent workflow as
``Child Workflows``.

If your deployment is using multi-cluster setup, then Child workflows, may also allow you to spread the workload of a workflow
potentially to multiple clusters.
"""

import typing
from typing import Tuple
from flytekit import task, workflow


# %%
# The task here also uses named outputs. Note that we always try and define NamedTuple as a separate type, as a best
# practice (though it can be defined inline)
op = typing.NamedTuple("OutputsBC", t1_int_output=int, c=str)


@task
def t1(a: int) -> op:
    return op(a + 2, "world")


# %%
# This will be the subworkflow of our examples, but note that this is a workflow like any other. It can be run just
# like any other workflow. Note here that the workflow has been declared with a default.
@workflow
def leaf_subwf(a: int = 42) -> Tuple[str, str]:
    x, y = t1(a=a).with_overrides(node_name="leafwf-n0")
    u, v = t1(a=x).with_overrides(node_name="leafwf-n1")
    return y, v


@workflow
def other_child_wf(a: int = 42) -> Tuple[int, str]:
    x, y = t1(a=a).with_overrides(node_name="other-child-n0")
    return x, y


# %%
# Example 1:
# ^^^^^^^^^^^
# This is the parent workflow. In it, we call the workflow declared above.
# This also showcases how to override the node name of a task (or subworkflow). Typically, nodes are just named
# sequentially, ``n0``, ``n1``, and so on. Because the inner ``my_subwf`` also has a ``n0`` you may
# wish to change the name of the first one. Not doing so is also fine - Flyte will automatically prepend something
# to the inner ``n0``, since node IDs need to be distinct within a workflow graph. This issue does not exist
# when calling something by launch plan since those launch a separate execution entirely.
#
# .. note::
#
#    Also note the use of with_overrides to provide a new name to the graph-node for better rendering or readability
@workflow
def parent_wf(a: int) -> Tuple[int, str, str]:
    x, y = t1(a=a).with_overrides(node_name="parent-n0")
    u, v = leaf_subwf(a=x).with_overrides(node_name="parent-n1")
    return x, u, v


@workflow
def other_parent_wf(a: int) -> Tuple[int, int, str]:
    x, y = t1(a=a).with_overrides(node_name="parent-n0")  # intentionally using the same name
    u, v = other_child_wf(a=x).with_overrides(node_name="parent-n1")  # intentionally using the same name
    return x, u, v


# %%
# You can execute subworkflows locally
if __name__ == "__main__":
    print(f"Running parent_wf(a=3) {parent_wf(a=3)}")


# %%
# Example 2:
# ^^^^^^^^^^
# You can also nest subworkflows in other subworkflows as shown in the following example. Also note, how workflows
# can be simply composed from other workflows, even if the other workflows are standalone entities. Each of the
# workflows in this module can exist independently and executed independently
@workflow
def root_level_wf(a: int) -> Tuple[int, str, str, str]:
    x, y = leaf_subwf(a=a).with_overrides(node_name="root-n0")
    m, n, o = parent_wf(a=a).with_overrides(node_name="root-n1")
    return m, n, o, y


@workflow
def other_root_wf(a: int) -> Tuple[int, str, str, int, int, str]:
    x, y, z = parent_wf(a=a).with_overrides(node_name="other-root-n0")
    aa, b, c = other_parent_wf(a=a).with_overrides(node_name="other-root-n1")
    return x, y, z, aa, b, c


# %%
# You can execute the nested workflows locally as well
if __name__ == "__main__":
    print(f"Running root_level_wf(a=3) {root_level_wf(a=3)}")
