# Copyright (c) 2018 Intel.
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

"""
Track resources like FPGA GPU and QAT for a host.  Provides the
conductor with useful information about availability through the accelerator
model.
"""

from oslo_config import cfg
from oslo_log import log as logging
from oslo_messaging.rpc.client import RemoteError
from oslo_utils import uuidutils
import socket
import uuid
import yaml

from cyborg.accelerator.drivers.fpga.base import FPGADriver
from cyborg.agent import rc_fields
from cyborg.common import utils
from cyborg import objects
from cyborg.objects.physical_function import PhysicalFunction
from cyborg.objects.virtual_function import VirtualFunction


CONF = cfg.CONF

LOG = logging.getLogger(__name__)

AGENT_RESOURCE_SEMAPHORE = "agent_resources"

DEPLOYABLE_VERSION = "1.0"

# need to change the driver field name
DEPLOYABLE_HOST_MAPS = {"assignable": "assignable",
                        "pcie_address": "devices",
                        "board": "product_id",
                        "type": "function",
                        "vendor": "vendor_id",
                        "name": "name"}

RC_FPGA = rc_fields.ResourceClass.normalize_name(
    rc_fields.ResourceClass.FPGA)

RESOURCES = {
    "fpga": RC_FPGA
}

class ResourceTracker(object):
    """Agent helper class for keeping track of resource usage as instances
    are built and destroyed.
    """

    def __init__(self, host, cond_api, placement_client):
        # FIXME (Shaohe) local cache for Accelerator.
        # Will fix it in next release.
        self.fpgas = None
        self.host = host
        self.conductor_api = cond_api
        self.fpga_driver = FPGADriver()
        self.p_client = placement_client

    @utils.synchronized(AGENT_RESOURCE_SEMAPHORE)
    def claim(self, context):
        pass

    def _fpga_compare_and_update(self, host_dev, acclerator):
        need_updated = False
        for k, v in DEPLOYABLE_HOST_MAPS.items():
            if acclerator[k] != host_dev[v]:
                need_updated = True
                acclerator[k] = host_dev[v]
        return need_updated

    def _gen_accelerator_for_deployable(
            self, context, name, vendor, productor, desc="", dev_type="pf",
            acc_type="FPGA", acc_cap="", remotable=0):
        """
        The type of the accelerator device, e.g GPU, FPGA, ...
        acc_type defines the usage of the accelerator, e.g Crypto
        acc_capability defines the specific capability, e.g AES
        """
        db_acc = {
            'deleted': False,
            'uuid': uuidutils.generate_uuid(),
            'name': name,
            'description': desc,
            'project_id': None,
            'user_id': None,
            'device_type': dev_type,
            'acc_type': acc_type,
            'acc_capability': acc_cap,
            'vendor_id': vendor,
            'product_id': productor,
            'remotable': remotable
            }

        acc = objects.Accelerator(context, **db_acc)
        acc = self.conductor_api.accelerator_create(context, acc)
        return acc

    def _gen_deployable_from_host_dev(
            self, host_dev, acc_id, parent_uuid=None, root_uuid=None):
        dep = {}
        for k, v in DEPLOYABLE_HOST_MAPS.items():
            dep[k] = host_dev[v]
        dep["host"] = self.host
        dep["version"] = DEPLOYABLE_VERSION
        dep["availability"] = "free"
        dep["uuid"] = uuidutils.generate_uuid()
        # dep["parent_uuid"] = parent_uuid if parent_uuid else dep["uuid"]
        # dep["root_uuid"] = root_uuid if root_uuid else dep["uuid"]
        dep["parent_uuid"] = parent_uuid
        dep["root_uuid"] = root_uuid
        dep["accelerator_id"] = acc_id
        return dep

    @utils.synchronized(AGENT_RESOURCE_SEMAPHORE)
    def update_usage(self, context):
        """Update the resource usage and stats after a change in an
        instance
        """
        def create_deployable(fpgas, bdf, parent_uuid=None):
            fpga = fpgas[bdf]
            dep = self._gen_deployable_from_host_dev(fpga)
            # if parent_uuid:
            dep["parent_uuid"] = parent_uuid
            obj_dep = objects.Deployable(context, **dep)
            new_dep = self.conductor_api.deployable_create(context, obj_dep)
            return new_dep

        afu_map = {}
        if CONF.find_file("afu_map.yaml"):
            with open('/etc/cyborg/afu_map.yaml') as fp:
                all_map = yaml.load(fp)
                afu_map = all_map.get("FPGA", {})

        # NOTE(Shaohe Feng) need more agreement on how to keep consistency.
        fpgas = self._get_fpga_devices(context)
        for f in self._get_fpga_devices_from_all_vendors(context):
            query = {"pcie_address": f["devices"],
                     "host": self.host, "name": f["name"]}
            pf_list = PhysicalFunction.get_by_filter(
                context, query)
            pf = pf_list[0] if pf_list else None
            regions = f.get("regions", [])
            if not pf:
                acc = self._gen_accelerator_for_deployable(
                    context,
                    f["name"], f["vendor_id"], f["product_id"],
                    "FPGA device on %s" % self.host, "pf", "FPGA")
                dep = self._gen_deployable_from_host_dev(f, acc.id)
                new_pf = PhysicalFunction(context, **dep)
                new_pf.create(context)
                new_pf.save(context)
                pf = new_pf
            if not pf.virtual_function_list and regions:
                for v in regions:
                    acc = self._gen_accelerator_for_deployable(
                        context,
                        v["name"], v["vendor_id"], v["product_id"],
                        "FPGA device on %s" % self.host, "vf", "FPGA")
                    dep = self._gen_deployable_from_host_dev(v, acc.id, pf.uuid)
                    new_vf = VirtualFunction(context, **dep)
                    new_vf.create(context)
                    new_vf.save(context)
                    pf.add_vf(new_vf)
                new_pf.save(context)
            for vf_obj in pf.virtual_function_list:
                for k, v in f["attrs"].items():
                    v_afu = afu_map.get(f["vendor_id"], {})
                    afu_name = v_afu.get(v, "")
                    if afu_name:
                        print("Find AFU %s name: %s from config file"
                              % (k, afu_name))
                    kwargs = {
                        "key": k,
                        "value": v,
                        "deployable_id": vf_obj.id,
                    }
                    if not objects.Attribute.get_by_filter(context, kwargs):
                        kwargs["uuid"] = uuidutils.generate_uuid()
                        attr = objects.Attribute(context, **kwargs)
                        attr.create(context)
                        vf_obj.add_attribute(attr)

                vf_obj.save(context)

        bdfs = set(fpgas.keys())
        deployables = self.conductor_api.deployable_get_by_host(
            context, self.host)

        # NOTE(Shaohe Feng) when no "pcie_address" in deployable?
        accls = dict([(v["pcie_address"], v) for v in deployables])
        accl_bdfs = set(accls.keys())

        # Firstly update
        for mutual in accl_bdfs & bdfs:
            accl = accls[mutual]
            if self._fpga_compare_and_update(fpgas[mutual], accl):
                try:
                    self.conductor_api.deployable_update(context, accl)
                except RemoteError as e:
                    LOG.error(e)
        # Add
        new = bdfs - accl_bdfs
        new_pf = set([n for n in new if fpgas[n]["function"] == "pf"])
        for n in new_pf:
            new_dep = create_deployable(fpgas, n)
            accls[n] = new_dep
            sub_vf = set()
            if "regions" in n:
                sub_vf = set([sub["devices"] for sub in fpgas[n]["regions"]])
            for vf in sub_vf & new:
                new_dep = create_deployable(fpgas, vf, new_dep["uuid"])
                accls[vf] = new_dep
                new.remove(vf)
        for n in new - new_pf:
            p_bdf = fpgas[n]["parent_devices"]
            p_accl = accls[p_bdf]
            p_uuid = p_accl["uuid"]
            new_dep = create_deployable(fpgas, n, p_uuid)

        # Delete
        for obsolete in accl_bdfs - bdfs:
            try:
                self.conductor_api.deployable_delete(context, accls[obsolete])
            except RemoteError as e:
                LOG.error(e)
            del accls[obsolete]

    # should be in driver
    def _gen_resource_inventory(self, name, total=0, max=0, min=1, step=1):
        """Update a ProviderTree object with current resource provider and
        inventory information.

        :param nova.compute.provider_tree.ProviderTree provider_tree:
            A nova.compute.provider_tree.ProviderTree object representing all
            the providers in the tree associated with the compute node, and any
            sharing providers (those with the ``MISC_SHARES_VIA_AGGREGATE``
            trait) associated via aggregate with any of those providers (but
            not *their* tree- or aggregate-associated providers), as currently
            known by placement.

        :param name:
            resource class name
        """
        # NOTE(sbauza): For the moment, the libvirt driver only supports
        # providing the total number of virtual GPUs for a single GPU type. If
        # you have multiple physical GPUs, each of them providing multiple GPU
        # types, libvirt will return the total sum of virtual GPUs
        # corresponding to the single type passed in enabled_vgpu_types
        # configuration option. Eg. if you have 2 pGPUs supporting 'nvidia-35',
        # each of them having 16 available instances, the total here will be
        # 32.
        # If one of the 2 pGPUs doesn't support 'nvidia-35', it won't be used.
        # TODO(sbauza): Use traits to make a better world.
        # vgpus = self._get_vgpu_total()

        # NOTE(jaypipes): We leave some fields like allocation_ratio and
        # reserved out of the returned dicts here because, for now at least,
        # the RT injects those values into the inventory dict based on the
        # compute_nodes record values.
        result = {}

        # If a sharing DISK_GB provider exists in the provider tree, then our
        # storage is shared, and we should not report the DISK_GB inventory in
        # the compute node provider.
        result[name] = {
                'total': total,
                'min_unit': min,
                'max_unit': max,
                'step_size': step,
            }
        return result


    def _get_fpga_devices(self, context):

        def form_dict(devices, fpgas):
            for v in devices:
                fpgas[v["devices"]] = v
                if "regions" in v:
                    form_dict(v["regions"], fpgas)

        fpgas = {}
        vendors = self.fpga_driver.discover_vendors()
        for v in vendors:
            driver = self.fpga_driver.create(v)
            devices = driver.discover()
            for dev in devices:
                total = len(dev["regions"]) if "regions" in dev else 1
                self.provider_report(context, dev["name"], RESOURCES["fpga"],
                                     dev["lables"], total, total)
                del dev["lables"]
            form_dict(devices, fpgas)
        return fpgas

    def _get_fpga_devices_from_all_vendors(self, context):
        fpgas = []
        vendors = self.fpga_driver.discover_vendors()
        for v in vendors:
            driver = self.fpga_driver.create(v)
            devices = driver.discover()
            fpgas.extend(devices)
        return fpgas

    def _get_root_provider(self):
        try:
            prvioder = self.p_client.get(
                "resource_providers?name="+socket.gethostname()).json()
            pr_uuid = prvioder["resource_providers"][0]["uuid"]
            return pr_uuid
        except IndexError:
            print("Error, provider '%s' can not be found"
                  % socket.gethostname())
        except Exception as e:
            print("Error, could not access placement. Details: %s" % e)
        return

    def _get_sub_provider(self, context, parent, name):
        HOSTNAME = socket.gethostname()
        sub_pr_uuid = str(uuid.uuid3(uuid.NAMESPACE_DNS, name))
        sub_pr = self.p_client.get_provider_tree_and_ensure_root(
            context, sub_pr_uuid,
            name=name, parent_provider_uuid=parent)
        return sub_pr, sub_pr_uuid

    def provider_report(self, context, name, resource_class,
                        traits, total=0, max=0, min=1, step=1):
        # need try:
        # if nova agent does start up, will not update placement.
        try:
            rs = self.p_client.get("/resource_classes", version='1.26')
        except Exception as e:
            self.p_client.ensure_resource_classes(context, resource_class)
            print("Error, could not access resource_classes. Details: %s" % e)
            return

        pr_uuid = self._get_root_provider()
        if pr_uuid:
            # set_inventory_for_provider()
            HOSTNAME = socket.gethostname()
            PR_NAME = name + "@" + HOSTNAME
            # we can also get sub_pr_uuid by sub_pr.get_provider_uuids()[-1]
            sub_pr, sub_pr_uuid = self._get_sub_provider(
                context, pr_uuid, PR_NAME)
            result = self._gen_resource_inventory(resource_class, total, max)
            sub_pr.update_inventory(PR_NAME, result)
            # We need to normalize inventory data for the compute node provider
            # (inject allocation ratio and reserved amounts from the
            # compute_node record if not set by the virt driver) because the
            # virt driver does not and will not have access to the compute_node
            # inv_data = sub_pr.data(PR_NAME).inventory
            # _normalize_inventory_from_cn_obj(inv_data, compute_node)
            # sub_pr.update_inventory(PR_NAME, inv_data)
            # Flush any changes.
            self.p_client.update_from_provider_tree(context, sub_pr)
            # traits = ["CUSTOM_FPGA_INTEL", "CUSTOM_FPGA_INTEL_ARRIA10",
            #           "CUSTOM_FPGA_REGION_INTEL_UUID",
            #           "CUSTOM_FPGA_FUNCTION_INTEL_UUID",
            #           "CUSTOM_PROGRAMMABLE",
            #           "CUSTOM_FPGA_NETWORK"]
            self.p_client.set_traits_for_provider(context, sub_pr_uuid, traits)
