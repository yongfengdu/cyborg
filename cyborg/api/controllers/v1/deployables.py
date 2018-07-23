# Copyright 2018 Huawei Technologies Co.,LTD.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import pecan
from pecan import rest
import re
from six.moves import http_client
import wsme
from pecan import expose as pexpose
from wsme import types as wtypes

from cyborg.api.controllers import base
from cyborg.api.controllers import link
from cyborg.api.controllers.v1 import types
from cyborg.api.controllers.v1 import utils as api_utils
from cyborg.api import expose
from cyborg.common import exception
from cyborg.common import policy
from cyborg import objects


_RC_FPGA = "CUSTOM_ACCELERATOR_FPGA"
# Querystring-related constants
_QS_RESOURCES = 'resources'
_QS_REQUIRED = 'required' # only support required, should be traits?
_QS_TRAITS= 'traits' # only support required, should be traits?
_QS_MEMBER_OF = 'member_of'
_QS_CYBORG = 'cyborg'
_QS_KEY_PATTERN = re.compile(
        r"^(%s)([1-9][0-9]*)?$" % '|'.join(
        (_QS_RESOURCES, _QS_TRAITS, _QS_MEMBER_OF)))

_QS_INTEL_REGION_PREFIX = "CUSTOM_FPGA_INTEL_REGION_"
_QS_INTEL_FPGA_MODEL_PREFIX = "CUSTOM_FPGA_INTEL_"
_QS_FPGA_FUNCTION_PREFIX = "CUSTOM_FPGA_"
_QS_INTEL_FUNCTION_PREFIX = "CUSTOM_FPGA_INTEL_FUNCTION_"
_QS_INTEL_FPGA= "CUSTOM_FPGA_INTEL"
_QS_INTEL_FPGA_ARRIA_RPEFIX = "ARRIA"
_QS_FPGA_PROGRAMMABLE = "CUSTOM_PROGRAMMABLE"
_QS_INTEL_AFUID_PATTERN = re.compile(
    r"^(%s)([a-fA-F0-9]{32,32})$" % _QS_INTEL_FUNCTION_PREFIX)
_QS_INTEL_AFUNAME_PATTERN = re.compile(
    r"^(%s)(.{1,128})$" % _QS_INTEL_FUNCTION_PREFIX)
_QS_INTEL_REGIONID_PATTERN = re.compile(
    r"^(%s)([a-fA-F0-9]{32,32})$" % _QS_INTEL_REGION_PREFIX)
_QS_INTEL_FPGA_ARRIA_PATTERN = re.compile(
    r"^(%s)(%s[0-9]{2,2})$" %
    (_QS_INTEL_FPGA_MODEL_PREFIX, _QS_INTEL_FPGA_ARRIA_RPEFIX))

VONDER_MAP = {"intel": "0x8086"}


def _get_request_spec(body):
    request_spec = {}
    for key, values in body.items():
        match = _QS_KEY_PATTERN.match(key)
        if match:
            prefix, suffix = match.groups()
            g_value = request_spec.setdefault(suffix,
                 {_QS_RESOURCES: {_RC_FPGA: 0},
                  _QS_TRAITS: {_QS_REQUIRED: []},
                  _QS_MEMBER_OF: [], _QS_CYBORG: []})
            if prefix == _QS_RESOURCES:
                for v in values:
                    rname, _, rnum = v.partition("=")
                    rnmu = int(0 if rnum else rnum)
                    if rname in g_value[prefix]:
                        g_value[prefix][rname] = rnum
            elif prefix == _QS_TRAITS:
                for v in values:
                    rname, _, rsetting = v.partition("=")
                    if rsetting == _QS_REQUIRED:
                        g_value[prefix][rsetting].append(rname)

    return request_spec

def gen_intel_fgpa_filter_from_traits(traits, host):
    filtes = {"vendor": VONDER_MAP["intel"]}
    if "CUSTOM_FPGA_INTEL" not in traits:
        return {}
    for tr in traits:
        m =  _QS_INTEL_AFUID_PATTERN.match(tr)
        if m:
            _, afu_id = m.groups()
            filtes["afu_id"] = afu_id
            continue
        mfm =  _QS_INTEL_AFUNAME_PATTERN.match(tr)
        if mfm and not m:
            _, afu_name = m.groups()
            filtes["afu_name"] = afu_name
            continue

        m = _QS_INTEL_REGIONID_PATTERN.match(tr)
        if m:
            _, region_id = m.groups()
            filtes["region_id"] = region_id
            continue
        m = _QS_INTEL_FPGA_ARRIA_PATTERN.match(tr)
        if m:
            _, model = m.groups()
            filtes["model"] = model.lower()
            continue
    filtes["host"] = host
    return filtes


class Deployable(base.APIBase):
    """API representation of a deployable.

    This class enforces type checking and value constraints, and converts
    between the internal object model and the API representation of
    a deployable.
    """

    uuid = types.uuid
    """The UUID of the deployable"""

    name = wtypes.text
    """The name of the deployable"""

    parent_uuid = types.uuid
    """The parent UUID of the deployable"""

    root_uuid = types.uuid
    """The root UUID of the deployable"""

    pcie_address = wtypes.text
    """The pcie address of the deployable"""

    host = wtypes.text
    """The host on which the deployable is located"""

    board = wtypes.text
    """The board of the deployable"""

    vendor = wtypes.text
    """The vendor of the deployable"""

    version = wtypes.text
    """The version of the deployable"""

    type = wtypes.text
    """The type of the deployable"""

    assignable = types.boolean
    """Whether the deployable is assignable"""

    instance_uuid = types.uuid
    """The UUID of the instance which deployable is assigned to"""

    availability = wtypes.text
    """The availability of the deployable"""

    links = wsme.wsattr([link.Link], readonly=True)
    """A list containing a self link"""

    def __init__(self, **kwargs):
        super(Deployable, self).__init__(**kwargs)
        self.fields = []
        for field in objects.Deployable.fields:
            self.fields.append(field)
            setattr(self, field, kwargs.get(field, wtypes.Unset))

    @classmethod
    def convert_with_links(cls, obj_dep):
        api_dep = cls(**obj_dep.as_dict())
        url = pecan.request.public_url
        api_dep.links = [
            link.Link.make_link('self', url, 'deployables', api_dep.uuid),
            link.Link.make_link('bookmark', url, 'deployables', api_dep.uuid,
                                bookmark=True)
            ]
        return api_dep


class DeployableCollection(base.APIBase):
    """API representation of a collection of deployables."""

    deployables = [Deployable]
    """A list containing deployable objects"""

    @classmethod
    def convert_with_links(cls, obj_deps):
        collection = cls()
        collection.deployables = [Deployable.convert_with_links(obj_dep)
                                  for obj_dep in obj_deps]
        return collection


class DeployablePatchType(types.JsonPatchType):

    _api_base = Deployable

    @staticmethod
    def internal_attrs():
        defaults = types.JsonPatchType.internal_attrs()
        return defaults + ['/pcie_address', '/host', '/type']


class Allocation(base.APIBase):
    """API representation of a deployable Allocation.

    This class enforces type checking and value constraints, and converts
    between the internal object model and the API representation of
    a deployable allocation.
    """

    uuid = types.uuid
    """The UUID of the deployable"""

    name = wtypes.text
    """The name of the deployable"""

    pcie_address = wtypes.text
    """The pcie address of the deployable"""

    host = wtypes.text
    """The host on which the deployable is located"""

    type = wtypes.text
    """The type of the deployable"""

    instance_uuid = types.uuid
    """The UUID of the instance which deployable is assigned to"""

    links = wsme.wsattr([link.Link], readonly=True)
    """A list containing a self link"""

    def __init__(self, **kwargs):
        super(Allocation, self).__init__(**kwargs)
        self.fields = []
        for field in objects.Deployable.fields:
            if hasattr(self, k) and getattr(self, k) != wsme.Unset:
                self.fields.append(field)
            setattr(self, field, kwargs.get(field, wtypes.Unset))

    @classmethod
    def convert_with_links(cls, obj_dep):
        api_dep = cls(**obj_dep.as_dict())
        url = pecan.request.public_url
        api_dep.links = [
            link.Link.make_link('self', url, 'deployables', api_dep.uuid),
            link.Link.make_link('bookmark', url, 'deployables', api_dep.uuid,
                                bookmark=True)
            ]
        return api_dep


class AllocationCollection(base.APIBase):
    """API representation of a collection of deployables."""

    allocations = [Allocation]
    """A list containing deployable objects"""

    @classmethod
    def convert_with_links(cls, obj_deps):
        collection = cls()
        collection.allocations = [Allocation.convert_with_links(obj_dep)
                                  for obj_dep in obj_deps]
        return collection

class AllocationsController(rest.RestController):
    """REST controller for Deployables Action."""

    # agentapi = AgentAPI()

    @expose.expose(DeployableCollection, body=types.jsontype)
    def post(self, dep):
        context = pecan.request.context
        instance_uuid = dep.get("instance_uuid")
        host = dep.get("host")
        if not instance_uuid or not host:
            # FIXME (Should raise exception)
            return DeployableCollection.convert_with_links([])
        # FIXME should use filter (such as host, and instance_uuid) for list.
        obj_deps = []
        specs = _get_request_spec(dep)

        for _, spec in specs.items():
            # only support "CUSTOM_ACCELERATOR_FPGA" this version
            res_num = int(spec[_QS_RESOURCES][_RC_FPGA])
            traits = spec[_QS_TRAITS][_QS_REQUIRED]
            filters = {}
            if res_num > 0:
                filters = gen_intel_fgpa_filter_from_traits(traits, host)
                filters["instance_uuid"] = None
                deps = objects.Deployable.get_by_filter(
                        pecan.request.context, filters)
                # null_deps = [dep for dep in deps if dep.instance_uuid]
                if len(deps) < res_num:
                    return DeployableCollection.convert_with_links([])
                obj_deps = obj_deps + deps[:res_num]

        for obj in obj_deps:
            obj["instance_uuid"] = instance_uuid
            new_dep = pecan.request.conductor_api.deployable_update(context, obj)

        return DeployableCollection.convert_with_links(obj_deps)


class DeployablesController(rest.RestController):
    """REST controller for Deployables."""
    allocations = AllocationsController()

    @policy.authorize_wsgi("cyborg:deployable", "create", False)
    @expose.expose(Deployable, body=types.jsontype,
                   status_code=http_client.CREATED)
    def post(self, dep):
        """Create a new deployable.

        :param dep: a deployable within the request body.
        """
        context = pecan.request.context
        obj_dep = objects.Deployable(context, **dep)
        new_dep = pecan.request.conductor_api.deployable_create(context,
                                                                obj_dep)
        # Set the HTTP Location Header
        pecan.response.location = link.build_url('deployables', new_dep.uuid)
        return Deployable.convert_with_links(new_dep)

    @policy.authorize_wsgi("cyborg:deployable", "get_one")
    @expose.expose(Deployable, types.uuid)
    def get_one(self, uuid):
        """Retrieve information about the given deployable.

        :param uuid: UUID of a deployable.
        """

        obj_dep = objects.Deployable.get(pecan.request.context, uuid)
        return Deployable.convert_with_links(obj_dep)

    @policy.authorize_wsgi("cyborg:deployable", "get_all")
    @expose.expose(DeployableCollection, int, types.uuid, wtypes.text,
                   wtypes.text, types.boolean)
    def get_all(self):
        """Retrieve a list of deployables."""
        obj_deps = objects.Deployable.list(pecan.request.context)
        return DeployableCollection.convert_with_links(obj_deps)

    @policy.authorize_wsgi("cyborg:deployable", "update")
    @expose.expose(Deployable, types.uuid, body=[DeployablePatchType])
    def patch(self, uuid, patch):
        """Update a deployable.

        :param uuid: UUID of a deployable.
        :param patch: a json PATCH document to apply to this deployable.
        """
        context = pecan.request.context
        obj_dep = objects.Deployable.get(context, uuid)

        try:
            api_dep = Deployable(
                **api_utils.apply_jsonpatch(obj_dep.as_dict(), patch))
        except api_utils.JSONPATCH_EXCEPTIONS as e:
            raise exception.PatchError(patch=patch, reason=e)

        # Update only the fields that have changed
        for field in objects.Deployable.fields:
            try:
                patch_val = getattr(api_dep, field)
            except AttributeError:
                # Ignore fields that aren't exposed in the API
                continue
            if patch_val == wtypes.Unset:
                patch_val = None
            if obj_dep[field] != patch_val:
                obj_dep[field] = patch_val

        new_dep = pecan.request.conductor_api.deployable_update(context,
                                                                obj_dep)
        return Deployable.convert_with_links(new_dep)

    @policy.authorize_wsgi("cyborg:deployable", "delete")
    @expose.expose(None, types.uuid, status_code=http_client.NO_CONTENT)
    def delete(self, uuid):
        """Delete a deployable.

        :param uuid: UUID of a deployable.
        """
        context = pecan.request.context
        obj_dep = objects.Deployable.get(context, uuid)
        pecan.request.conductor_api.deployable_delete(context, obj_dep)
