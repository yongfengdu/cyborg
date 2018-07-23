# Copyright 2018 Intel, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.


"""
Cyborg Intel FPGA driver implementation.
"""

# from cyborg.accelerator.drivers.fpga.base import FPGADriver

import glob
import os
import re


SYS_FPGA = "/sys/class/fpga"
DEVICE = "device"
PF = "physfn"
VF = "virtfn*"
BDF_PATTERN = re.compile(
    "^[a-fA-F\d]{4}:[a-fA-F\d]{2}:[a-fA-F\d]{2}\.[a-fA-F\d]$")


DEVICE_FILE_MAP = {"vendor": "vendor_id",
                   "device": "product_id",
                   "sriov_numvfs": "pr_num"}
DEVICE_FILE_HANDLER = {}
DEVICE_EXPOSED = ["vendor", "device", "sriov_numvfs"]


def all_fpgas():
    # glob.glob1("/sys/class/fpga", "*")
    return glob.glob(os.path.join(SYS_FPGA, "*"))


def all_vf_fpgas():
    return [dev.rsplit("/", 2)[0] for dev in
            glob.glob(os.path.join(SYS_FPGA, "*/device/physfn"))]


def all_pure_pf_fpgas():
    return [dev.rsplit("/", 2)[0] for dev in
            glob.glob(os.path.join(SYS_FPGA, "*/device/virtfn0"))]


def target_symbolic_map():
    maps = {}
    for f in glob.glob(os.path.join(SYS_FPGA, "*/device")):
        maps[os.path.realpath(f)] = os.path.dirname(f)
    return maps


def bdf_path_map():
    maps = {}
    for f in glob.glob(os.path.join(SYS_FPGA, "*/device")):
        maps[os.path.basename(os.path.realpath(f))] = os.path.dirname(f)
    return maps


def all_vfs_in_pf_fpgas(pf_path):
    maps = target_symbolic_map()
    vfs = glob.glob(os.path.join(pf_path, "device/virtfn*"))
    return [maps[os.path.realpath(vf)] for vf in vfs]


def all_pf_fpgas():
    return [dev.rsplit("/", 2)[0] for dev in
            glob.glob(os.path.join(SYS_FPGA, "*/device/sriov_totalvfs"))]


def is_vf(path):
    return True if glob.glob(os.path.join(path, "device/physfn")) else False


def find_pf_by_vf(path):
    maps = target_symbolic_map()
    p = os.path.realpath(os.path.join(path, "device/physfn"))
    return maps[p]


def is_bdf(bdf):
    return True if BDF_PATTERN.match(bdf) else False


def get_bdf_by_path(path):
    return os.path.basename(os.readlink(os.path.join(path, "device")))


def split_bdf(bdf):
    return ["0x" + v for v in bdf.replace(".", ":").rsplit(":")[1:]]


def get_pf_bdf(bdf):
    path = bdf_path_map().get(bdf)
    if path:
        path = find_pf_by_vf(path) if is_vf(path) else path
        return get_bdf_by_path(path)
    return bdf


def fpga_device(path):
    infos = {}

    def read_line(filename):
        with open(filename) as f:
            return f.readline().strip()

    # NOTE "In 3.x, os.path.walk is removed in favor of os.walk."
    for (dirpath, dirnames, filenames) in os.walk(path):
        for filename in filenames:
            if filename in DEVICE_EXPOSED:
                key = DEVICE_FILE_MAP.get(filename) or filename
                if key in DEVICE_FILE_HANDLER and callable(
                        DEVICE_FILE_HANDLER(key)):
                    infos[key] = DEVICE_FILE_HANDLER(key)(
                        os.path.join(dirpath, filename))
                else:
                    infos[key] = read_line(os.path.join(dirpath, filename))
    return infos


def get_afu_ids(name):
    ids = []
    for path in glob.glob(os.path.join(
            SYS_FPGA, name, "intel-fpga-port.*", "afu_id")):
        with open(path) as f:
            first_line = f.readline()
            ids.append(first_line.split('\n', 1)[0])
    return ids


def get_labels_and_attrs(name, product_id, extra={}):
    # "region_id" not support at present, "CUSTOM_FPGA_REGION_INTEL_UUID"
    # "CUSTOM_PROGRAMMABLE" not support at present
    labels = ["CUSTOM_FPGA_INTEL"]
    attrs = {}
    funcs = extra.get("INTEL_FUNCTION", {})
    models = extra.get("INTEL_MODEL", {})
    for i in get_afu_ids(name):
        l = "CUSTOM_FPGA_INTEL_FUNCTION_" + i
        labels.append(l)
        # FIXME only support one id at present.
        attrs["afu_id"] = l
        fn = extra.get(i, "")
        if fn:
            labels.append("CUSTOM_FPGA_INTEL_FUNCTION_" + fn)
            attrs["afu_name"] = fn
    model = extra.get(product_id, "")
    if model:
        labels.append("CUSTOM_FPGA_INTEL_MODEL_" + model)
        attrs["model"] = model.lower()
    return {"labels": labels,
             "attrs": attrs}


def fpga_tree(extra):

    def gen_fpga_infos(path, vf=True):
        name = os.path.basename(path)
        dpath = os.path.realpath(os.path.join(path, DEVICE))
        bdf = os.path.basename(dpath)
        func = "vf" if vf else "pf"
        pf_bdf = os.path.basename(
            os.path.realpath(os.path.join(dpath, PF))) if vf else ""
        fpga = {"path": path, "function": func,
                "devices": bdf, "assignable": True,
                "parent_devices": pf_bdf,
                "name": name,
                }
        d_info = fpga_device(dpath)
        fpga.update(d_info)
        l_a = get_labels_and_attrs(fpga["name"], fpga["product_id"], extra)
        fpga.update({"labels": l_a["labels"], "attrs": l_a["attrs"]})
        return fpga

    devs = []
    pure_pfs = all_pure_pf_fpgas()
    for pf in all_pf_fpgas():
        fpga = gen_fpga_infos(pf, False)
        if pf in pure_pfs:
            fpga["assignable"] = False
            fpga["regions"] = []
            vfs = all_vfs_in_pf_fpgas(pf)
            for vf in vfs:
                vf_fpga = gen_fpga_infos(vf, True)
                fpga["regions"].append(vf_fpga)
        devs.append(fpga)
    return devs
