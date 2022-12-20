#!/usr/bin/env python
#
# CLUES - Cluster Energy Saving System
# Copyright (C) 2015 - GRyCAP - Universitat Politecnica de Valencia
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import json
import clueslib.configlib
import logging
import clueslib.helpers
from cpyutils.runcommand import runcommand

from clueslib.platform import LRMS
from clueslib.node import NodeInfo
from cpyutils.evaluate import TypedClass, TypedList
import collections

import cpyutils.log
_LOGGER = cpyutils.log.Log("PLUGIN-PBS")

def _translate_mem_value(memval):
    memval = memval.lower().rstrip(".").strip()
    
    multiplier = 1
    if len(memval) > 0:
        qualifier = memval[-2:]
        if qualifier == 'kb':
            multiplier = 1024
        elif qualifier == 'mb':
            multiplier = 1024*1024
        elif qualifier == 'gb':
            multiplier = 1024*1024*1024
        elif qualifier == 'tb':
            multiplier = 1024*1024*1024*1024
        elif qualifier == 'pb':
            multiplier = 1024*1024*1024*1024*1024
        
    if multiplier > 1:
        value_str = memval[:-2]
    else:
        value_str = memval
    
    try:
        value = int(value_str)
    except:
        try:
            value = float(value_str)
        except:
            value = -1
            
    return value * multiplier


# This function facilitates the parsing of the scontrol command exit
def parse_scontrol(out):
    out = out.decode()
    if out.find("=") < 0: return []
    r = []
    for line in out.split("\n"):
        line = line.strip()
        if not line: continue
        d = {}
        while line:
            item = ""
            while "=" not in item:
                split_val = line.rsplit(" ", 1)
                # in the last case split_val only has 1 elem
                elem = split_val[-1]
                line = split_val[0] if len(split_val) == 2 else ""
                if "=" not in item and item:
                    item = "%s %s" % (elem, item)
                else:
                    item += elem
            k,v = item.split("=", 1)
            d[k] = v.strip()
        r.append(d)
    return r


# TODO: consider states in the second line of slurm
# Function that translates the slurm node state into a valid clues2 node state
def infer_clues_node_state(state,slots_count,slots_free):
    res_state = ""
    states = state.split(',')
    
    for state in states:
        state = state.strip()
        
        if state == 'free': res_state = NodeInfo.IDLE
        elif state == 'offline': res_state = NodeInfo.OFF
        elif state == 'down': res_state = NodeInfo.OFF
        elif state == 'job-exclusive' or state == 'busy' or state == 'job-busy' or state == 'reserve' or state == 'job-reserve': res_state = NodeInfo.USED
        else: res_state = NodeInfo.OFF
       
        # Si ya estamos en estado down, no seguimos mirando
        if res_state == NodeInfo.OFF:
            break;
            
        if (res_state == NodeInfo.IDLE) and (slots_count > slots_free):
            res_state = NodeInfo.USED
        
    return res_state

# Function that translates the slurm job state into a valid clues2 job state
def infer_clues_job_state(state):
    # a job can be in several states
    # SLURM job states: CANCELLED, COMPLETED, CONFIGURING, COMPLETING, FAILED, NODE_FAIL, PENDING, PREEMPTED, RUNNING, SUSPENDED, TIMEOUT
    # CLUES2 job states: ATTENDED o PENDING
    res_state = ""

    if state == 'Q':
        res_state = clueslib.request.Request.PENDING
    else:
        res_state = clueslib.request.Request.ATTENDED

    return res_state

class lrms(LRMS):

    #def __init__(self, SLURM_SERVER = None, SLURM_PARTITION_COMMAND = None, SLURM_NODES_COMMAND = None, SLURM_JOBS_COMMAND = None):
    def __init__(self, PBS_SERVER = None, PBS_QSTAT_COMMAND = None, PBS_PBSNODES_COMMAND = None): # PBS_PATH = None):
        import cpyutils.config
        #config_slurm = cpyutils.config.Configuration(
        #    "SLURM",
        #    {
        #        "SLURM_SERVER": "slurmserverpublic", 
        #        "SLURM_PARTITION_COMMAND": "/usr/local/bin/scontrol -o show partitions",
        #        "SLURM_NODES_COMMAND": "/usr/local/bin/scontrol -o show nodes",
        #        "SLURM_JOBS_COMMAND": "/usr/local/bin/scontrol -o show jobs"
        #    }
        #)
        config_pbs = cpyutils.config.Configuration(
            "PBS",
            {
                "PBS_SERVER": "localhost", 
                "PBS_QSTAT_COMMAND": "/usr/bin/qstat",
                "PBS_PBSNODES_COMMAND": "/usr/bin/pbsnodes"
            }
        )
        
        #self._server_ip = clueslib.helpers.val_default(SLURM_SERVER, config_slurm.SLURM_SERVER)
        #self._partition  = clueslib.helpers.val_default(SLURM_PARTITION_COMMAND, config_slurm.SLURM_PARTITION_COMMAND)
        #self._nodes = clueslib.helpers.val_default(SLURM_NODES_COMMAND, config_slurm.SLURM_NODES_COMMAND)
        #self._jobs = clueslib.helpers.val_default(SLURM_JOBS_COMMAND, config_slurm.SLURM_JOBS_COMMAND)
        #clueslib.platform.LRMS.__init__(self, "SLURM_%s" % self._server_ip)
        
        self._server_ip = clueslib.helpers.val_default(PBS_SERVER, config_pbs.PBS_SERVER)
        _qstat_cmd = clueslib.helpers.val_default(PBS_QSTAT_COMMAND, config_pbs.PBS_QSTAT_COMMAND)
        self._qstat = _qstat_cmd.split(" ")
        _pbsnodes_cmd = clueslib.helpers.val_default(PBS_PBSNODES_COMMAND, config_pbs.PBS_PBSNODES_COMMAND)
        self._pbsnodes = _pbsnodes_cmd.split(" ")
        clueslib.platform.LRMS.__init__(self, "PBS_%s" % self._server_ip)

    # Function that recovers the partitions of a node
    # A node can be in several queues: SLURM has supported configuring nodes in more than one partition since version 0.7.0
    def _get_partition(self, node_name):

        '''Exit example of scontrol show partitions: 
        PartitionName=wn
        AllowGroups=ALL AllowAccounts=ALL AllowQos=ALL
        AllocNodes=ALL Default=NO
        DefaultTime=NONE DisableRootJobs=NO GraceTime=0 Hidden=NO
        MaxNodes=UNLIMITED MaxTime=UNLIMITED MinNodes=1 LLN=NO MaxCPUsPerNode=UNLIMITED
        Nodes=wn[0-4]
        Priority=1 RootOnly=NO ReqResv=NO Shared=NO PreemptMode=OFF
        State=UP TotalCPUs=5 TotalNodes=5 SelectTypeParameters=N/A
        DefMemPerNode=UNLIMITED MaxMemPerNode=UNLIMITED'''
        
        res_queue = []
        exit = ""

        try:
            command = self._pbsnodes + [ '-av', '-S', '-L', '-F json', self._server_ip ]
            #success, out = runcommand(command)
            success, out_command = cpyutils.runcommand.runcommand(command, False, timeout = clueslib.configlib._CONFIGURATION_GENERAL.TIMEOUT_COMMANDS)
            if not success:
                _LOGGER.error("could not get information about the queues: %s" % out_command)
                return None
            else:
                exit = parse_scontrol(out)
        except Exception as ex:
            _LOGGER.error("could not obtain information about SLURM partitions %s (%s)" % (self._server_ip, exit))
            return None
        
        if exit:
            for key in exit:
                nodes = str(key["Nodes"])
                if nodes == node_name:
                    #nodes is like wn1
                    res_queue.append(key["PartitionName"])
                else:
                    #nodes is like wnone-[0-1]
                    pos1 = nodes.find("[")
                    pos2 = nodes.find("]")
                    pos3 = nodes.find("-", pos1)
                    if pos1 > -1 and pos2 > -1 and pos3 > -1:
                        num1 = int(nodes[pos1+1:pos3])
                        num2 = int(nodes[pos3+1:pos2])
                        name = nodes[:pos1]
                        while num1 <= num2:
                            nodename = name + str(num1)
                            if nodename == node_name:
                                res_queue.append(key["PartitionName"])
                                break;
                            num1 = num1 + 1

        return res_queue

    def get_nodeinfolist(self):      
        nodeinfolist = collections.OrderedDict()
        
        '''Exit example of scontrol show nodes
        NodeName=wn0 Arch=x86_64 CoresPerSocket=1
        CPUAlloc=0 CPUErr=0 CPUTot=1 CPULoad=0.02 Features=(null)
        Gres=(null)
        NodeAddr=wn0 NodeHostName=wn0 Version=14.11
        OS=Linux RealMemory=1 AllocMem=0 Sockets=1 Boards=1
        State=IDLE ThreadsPerCore=1 TmpDisk=0 Weight=1
        BootTime=2015-04-28T13:12:21 SlurmdStartTime=2015-04-28T13:16:32
        CurrentWatts=0 LowestJoules=0 ConsumedJoules=0
        ExtSensorsJoules=n/s ExtSensorsWatts=0 ExtSensorsTemp=n/s'''


        command = self._pbsnodes + [ '-av', '-F', 'json', '-s', self._server_ip ]
        success, out_command = cpyutils.runcommand.runcommand(command, False, timeout = clueslib.configlib._CONFIGURATION_GENERAL.TIMEOUT_COMMANDS)
        if not success:
            #_LOGGER.error("could not obtain information about SLURM nodes %s (command rc != 0)" % self._server_ip)
            _LOGGER.error("could not get information about the queues: %s" % out_command)
            return None
        out_command_json = json.loads(out_command.decode())
        
        
        
        
        if out_command_json:
            for key in out_command_json["nodes"]:
                try:
                    name = str(key)
                    slots_count = 0
                    if "ncpus" in out_command_json["nodes"][key]["resources_available"]: 
                        slots_count = int(out_command_json["nodes"][key]["resources_available"]["ncpus"])
                    slots_ass = 0
                    if "ncpus" in out_command_json["nodes"][key]["resources_assigned"]: 
                        slots_ass = int(out_command_json["nodes"][key]["resources_assigned"]["ncpus"])
                    slots_free = slots_count - slots_ass
                    #NOTE: memory is in GB
                    memory_total = "0kb"
                    if "mem" in out_command_json["nodes"][key]["resources_available"]: 
                        memory_total = str(out_command_json["nodes"][key]["resources_available"]["mem"])
                    memory_ass = "0kb"
                    if "mem" in out_command_json["nodes"][key]["resources_assigned"]: 
                        memory_ass = str(out_command_json["nodes"][key]["resources_assigned"]["mem"])
                    memory_total = _translate_mem_value(memory_total)
                    memory_free = memory_total - _translate_mem_value(memory_ass)
                    state = infer_clues_node_state(str(out_command_json["nodes"][key]["state"]),slots_count,slots_free)
                    keywords = {}
                    queues = ""
                    if "queue" in out_command_json["nodes"][key]: 
                        queues = out_command_json["nodes"][key]["queue"]
                    keywords['hostname'] = TypedClass.auto(name)
                    if queues:
                        keywords['queues'] = TypedList([TypedClass.auto(q) for q in queues])
                        
                    nodeinfolist[name] = NodeInfo(name, slots_count, slots_free, memory_total, memory_free, keywords)
                    nodeinfolist[name].state = state
                except:
                    _LOGGER.error("Error adding node: %s." % key)

        return nodeinfolist

    # Method in charge of monitoring the job queue of SLURM
    def get_jobinfolist(self):

        command = self._qstat + [ '-f','-F', 'json', '@%s' % self._server_ip ]
        success, out_command = cpyutils.runcommand.runcommand(command, False, timeout = clueslib.configlib._CONFIGURATION_GENERAL.TIMEOUT_COMMANDS)

        if not success:
            _LOGGER.error("could not obtain information about PBS server %s (%s)" % (self._server_ip, out_command))
            return None

        out_command_json = json.loads(out_command.decode())
        jobinfolist = []
        if out_command_json and "Jobs" in out_command_json:
            for job in out_command_json["Jobs"]:
                try:
                    job_id = str(job)
                    state = infer_clues_job_state(str(out_command_json["Jobs"][job]["job_state"]))
                    nodes = []
                    memory = 0
                    cpus_per_task = 1
                    # ReqNodeList is also available
                    #if str(job["NodeList"]) != "(null)":
                    #    nodes.append(str(job["NodeList"]))
                    #if len(job["NumNodes"]) > 1:
                    #    numnodes = int(job["NumNodes"][:1])
                    #else:
                    #    numnodes = int(job["NumNodes"])
                    ## It seems that in some cases MinMemoryNode does not appear
                    #if 'MinMemoryNode' in job:
                    #    memory = _translate_mem_value(job["MinMemoryNode"] + ".MB")
                    #else:
                    #    memory = 0
                    #if 'NumTasks' in job:
                    #    numtasks = int(job["NumTasks"])
                    #else:
                    #    numtasks = numnodes
                    #cpus_per_task = int(job["CPUs/Task"])
                    numtasks = int(out_command_json["Jobs"][job]["resources_used"]["ncpus"])
                    partition = '"' + str(out_command_json["Jobs"][job]["queue"]) + '" in queues'
    
                    resources = clueslib.request.ResourcesNeeded(cpus_per_task, memory, [partition], numtasks)
                    j = clueslib.request.JobInfo(resources, job_id, nodes)
                    j.set_state(state)
                    jobinfolist.append(j)
                except:
                    _LOGGER.error("Error processing job: %s." % job)
        
        return jobinfolist
        
if __name__ == '__main__':
    pass
