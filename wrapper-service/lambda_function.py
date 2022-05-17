#!/usr/bin/env python3
import boto3, json, random, requests, os, signal, sys 
from time import sleep

from kubernetes_tools import kubernetes_tools
from kubernetes.client.rest import ApiException

# Global variables
gamelift = boto3.client('gamelift', region_name=os.getenv('AWS_REGION'))
ec2 = boto3.client('ec2', region_name=os.getenv('AWS_REGION'))
autoscaling = boto3.client('autoscaling', region_name=os.getenv('AWS_REGION'))

print_messageId=kubernetes_tools.print_messageId
set_messageId=kubernetes_tools.set_messageId

def deregister_game_server(GameServerGroupName: str, GameServerId: str):
    """A handler for when the daemon receives a SIGTERM signal.  Before shutting down, the daemon will deregister the instance from FleetIQ."""
    # degister game server on exit
    try: 
        gamelift.deregister_game_server(
            GameServerGroupName=GameServerGroupName,
            GameServerId=GameServerId
        )
        print_messageId(f'Degister Gamelift game server {GameServerId} health', flush=True)
        return True
    except gamelift.exceptions.NotFoundException as e:
        print_messageId(f'Instance {GameServerId} has already been deregistered', flush=True)
    return False

def decribe_instance(game_server):
    print_messageId(f'{game_server["InstanceId"]}:{game_server["InstanceStatus"]}', flush=True)
    Reservations = ec2.describe_instances(InstanceIds=[game_server["InstanceId"]]).get("Reservations")
    #print(json.dumps(Reservations), flush=True)

    for reservation in Reservations:
        for instance in reservation['Instances']:
            game_server["PrivateDnsName"] = instance.get("PrivateDnsName")
            game_server["PrivateIpAddress"] = instance.get("PrivateIpAddress")
            game_server["PublicDnsName"] = instance.get("PublicDnsName")
            game_server["PublicIpAddress"] = instance.get("PublicIpAddress") 
            
    return game_server
    
def describe_game_server_instances(params):
    group = params.get('group')
    paginator = gamelift.get_paginator('describe_game_server_instances')
    pages = paginator.paginate(GameServerGroupName=group)
    
    results = []
    for page in pages:
        for game_server in page['GameServerInstances']:
            game_server = decribe_instance(game_server)
            if 'PrivateDnsName' in game_server and len(game_server['PrivateDnsName']) > 0:
                results.append(game_server)
    
    return results      

def deregister_game_server_instances(params):
    group = params.get('group')
    paginator = gamelift.get_paginator('describe_game_server_instances')
    pages = paginator.paginate(GameServerGroupName=group)

    results = []
    for page in pages:
        for game_server in page['GameServerInstances']:
            if game_server["InstanceStatus"] != 'ACTIVE':
                if deregister_game_server(group, game_server["InstanceId"]) == True:
                    pass
                results.append(game_server["InstanceId"])        
    return results

def list_node(params):
    results = describe_game_server_instances(params)
    nodes = []
    for game_server in results:
        if 'PrivateDnsName' in game_server:
            PrivateDnsName = game_server["PrivateDnsName"]
            try: 
                node = kubernetes_tools.core_v1_client.read_node(PrivateDnsName)
            except ApiException as e:
                print_messageId(f'Exception when calling CoreV1Api->read_node: {e}\n', flush=True)
                return False
            if node.spec is None:
                continue
            nn = {}
            nn['node_name'] = PrivateDnsName
            nn['instance_id'] = game_server["InstanceId"]
            nn['taints'] = []
            for taint in node.spec.taints:
                tt = {}
                tt['node_name'] = PrivateDnsName
                tt['effect'] = taint.effect
                tt['key'] = taint.key
                tt['value'] = taint.value
                nn['taints'].append(tt)
            nodes.append(nn)

    return nodes
    pass

def read_namespaced_pod(params):
    custom_objs = list_cluster_custom_object_all()
    
    pods = []
    for item in custom_objs['items']:
        PodName = item['metadata']['name']
        PodNamespace = item['metadata']['namespace']
        try:
            pod = kubernetes_tools.core_v1_client.read_namespaced_pod(name=PodName, namespace=PodNamespace)
            print(pod.spec.tolerations)
        except ApiException as e:
            print_messageId(f'Exception when calling CoreV1Api->read_namespaced_pod: {e}\n', flush=True)

        pp = {}
        pp['node_name'] = pod.spec.node_name
        pp['pod_name'] = PodName
        pp['namespace'] = PodNamespace
        pp['name'] = pod.metadata.name
        pp['labels'] = pod.metadata.labels
        pp['tolerations'] = []
        for toler in pod.spec.tolerations:
            tt = {}
            tt['effect'] = toler.effect
            tt['operator'] = toler.operator
            tt['key'] = toler.key
            tt['value'] = toler.value
            tt['toleration_seconds'] = toler.toleration_seconds
            pp['tolerations'].append(tt)
        pods.append(pp)

    return pods
    pass

def list_game_server(params):
    custom_objs = list_cluster_custom_object_all()
    if custom_objs is None:
        return []  
    game_servers = custom_objs['items']
    return list(map(lambda x: x['status'], game_servers))
    pass

def list_cluster_custom_object_all():
    try:
        custom_objs = kubernetes_tools.custom_obj_client.list_cluster_custom_object(
            group='agones.dev', 
            version='v1', 
            plural='gameservers')
    except ApiException as e:
        print_messageId(f'Exception when calling CustomObjectsApi->list_cluster_custom_object: {e}')
    return custom_objs

def allocate_game_server(params):
    num = params.get('num')

    contentBody={
        "apiVersion": "allocation.agones.dev/v1",
        "kind": "GameServerAllocation",
        "spec": {
            "required": {
                "matchLabels":{
                    "agones.dev/fleet": "minetest"
                }
            }
        }
    }

    results = []
    for i in range(int(num)):
        try:
            result = kubernetes_tools.custom_obj_client.create_namespaced_custom_object(
                group='allocation.agones.dev', 
                version='v1', 
                namespace="default",
                plural='gameserverallocations',
                body=contentBody)
        except ApiException as e:
                print_messageId(f'Exception when calling CustomObjectsApi->create_namespaced_custom_object: {e}\n', flush=True)   
        results.append(result)
    return results

def default_handler(params):
    pass

actiondict = {
    'describe_game_server_instances': describe_game_server_instances,
    'deregister_game_server_instances': deregister_game_server_instances,
    'list_node': list_node,
    'read_namespaced_pod': read_namespaced_pod,
    'list_game_server': list_game_server,
    'allocate_game_server': allocate_game_server
}

def getAction(action, params):
    fun = actiondict.get(action, default_handler)
    return fun(params)
    
def lambda_handler(event, context):
    queryStringParameters = event['queryStringParameters']
    rawQueryString = event['rawQueryString']
    action = queryStringParameters.get('action')
    set_messageId(action,rawQueryString)

    if action is None:
        return 
    print_messageId("START!!!!")
    
    response = getAction(action, queryStringParameters)
    
    print_messageId("END!!!!")
    return {
        'statusCode': 200,
        'body': json.dumps(response)
    }
