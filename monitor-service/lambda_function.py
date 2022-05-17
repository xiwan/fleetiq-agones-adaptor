#!/usr/bin/env python3
import boto3, json, random, requests, os, signal, sys 
import redis
from time import sleep

import click
from ec2_metadata import ec2_metadata
from kubernetes_tools import kubernetes_tools
from kubernetes.client.rest import ApiException

# Global variables
gamelift = boto3.client('gamelift', region_name=os.getenv('AWS_REGION'))
ec2 = boto3.client('autoscaling', region_name=os.getenv('AWS_REGION'))

print_messageId=kubernetes_tools.print_messageId
set_messageId=kubernetes_tools.set_messageId

def redis_conn():
    try:
        r = redis.from_url('redis://' + os.getenv('REDIS_URL'))
        if r.ping() == True:
            print_messageId(f'Connection to database {os.getenv("REDIS_URL")} was successful', flush=True)
    except redis.RedisError as e:
        print_messageId(f'Could not connect to redis\n{e}', flush=True)
    return r

def is_healthy(InstanceId: str):
    """This method calls the DescribeAutoscalingInstance API to get the health status of the instance."""
    asg_instance = ec2.describe_auto_scaling_instances(
        InstanceIds=[
            InstanceId
        ]
    )
    instance_health = asg_instance['AutoScalingInstances'][0]['HealthStatus']
    print_messageId(f'The instance is {instance_health}', flush=True)
    return instance_health 

def is_ready_shutdown_check(PrivateDnsName: str):
    filtered_list = kubernetes_tools.get_game_servers(PrivateDnsName)
    print_messageId(f'There are {len(filtered_list)} Allocated game servers running on this instance', flush=True)
    if filtered_list == []:
        return True   
    return False

def cordon_and_protect(PrivateDnsName: str, InstanceId: str):
    """This method cordons the node, adds an toleration to all of the Agones game servers that are currently in an Allocated
    state, and then adds a taint to the node that evits pods the don't tolerate the taint."""
    cordon_body = {
        "spec": {
            "unschedulable": True
        }
    }
    # Cordon the node so no new game servers are scheduled onto the instance
    kubernetes_tools.patch_node(InstanceId, PrivateDnsName, json.dumps(cordon_body))
    toleration = {
        "effect": "NoExecute",
        "key": "gamelift.status/draining",
        "operator": "Equal",
        "value": "true"
    }
    filtered_list = kubernetes_tools.get_game_servers(PrivateDnsName)
    for item in filtered_list:
        print_messageId(f"Updating {item['metadata']['namespace']}/{item['metadata']['name']} with toleration", flush=True)
        kubernetes_tools.taint_pod(item['metadata']['name'], item['metadata']['namespace'], json.dumps(toleration))
    
    # Change taint to DRAINING
    taint = {
        "key": "gamelift.status/draining",
        "value": "true",
        "effect": "NoExecute"
    }
    return kubernetes_tools.taint_node(InstanceId, PrivateDnsName, json.dumps(taint))


def initialize_game_server(message):
    """This method registers the instance as a game server with Gamelift FleetIQ using the instance's Id as game server name.
    After registering the instance, it looks at result of DescribeAutoscalingInstances to see whether the instance is HEALTHY. 
    When HEALTHY, the instance is CLAIMED and its status is changed to UTILIZED. Finally, the taint gamelift.aws/status:ACTIVE,NoExecute
    is added to the node. Agones game servers need to have a toleration for this taint before they can run on this instance."""
    status = message['InstanceStatus']
    GameServerGroupName = message['GameServerGroupName']
    GameServerId = message['InstanceId']
    InstanceId = message['InstanceId']
    PrivateDnsName = message['PrivateDnsName']

    try:
        # Register game server instance
        print_messageId(f'Registering game server {GameServerId} PrivateDnsName: {PrivateDnsName}', flush=True)  
        gamelift.register_game_server(
            GameServerGroupName=GameServerGroupName,
            GameServerId=GameServerId,
            InstanceId=InstanceId
        )
    except gamelift.exceptions.ConflictException as error:
        print_messageId(f'The game server {GameServerId} is already registered', flush=True)
        #return None
    except Exception as e: 
        print_messageId(f'{e}', flush=True)
        deregister_game_server(GameServerGroupName, GameServerId)
        return None
    
    # Update the game server status to healthy
    # TODO Change this to use the new FleetIQ API DescribeGameServerInstances
    # TODO Consider using a decorator and backoff library to implement the backoff
    trytime = 0
    backoff = random.randint(1,5)
    while is_healthy(InstanceId) != 'HEALTHY':
        print_messageId(f'Instance is not healthy, re-trying in {backoff}', flush=True)
        sleep(backoff)
        trytime = trytime + 1
        # tried 3 times, let's return here
        if trytime == 3:
            return None
    
    update_health_status(message)
    
    # Claim the game server
    print_messageId(f'Claiming game server {GameServerId}', flush=True)
    try: 
        gamelift.claim_game_server(
            GameServerGroupName=GameServerGroupName,
            GameServerId=GameServerId
        )
    except gamelift.exceptions.ConflictException as error: 
        print_messageId('The instance has already been claimed', flush=True)
        
    # Update game server status 
    print_messageId(f'Changing server {GameServerId} status to utilized', flush=True)
    gamelift.update_game_server(
        GameServerGroupName=GameServerGroupName,
        GameServerId=GameServerId,
        UtilizationStatus='UTILIZED'
    )

    # # Adding taint ACTIVE to node
    # taint = {
    #     "key": "gamelift.status/active",
    #     "value": "true",
    #     "effect": "NoExecute"
    # }
    # kubernetes_tools.taint_node(InstanceId, PrivateDnsName, json.dumps(taint))

def update_health_status(message):
    status = message['InstanceStatus']
    GameServerGroupName = message['GameServerGroupName']
    GameServerId = message['InstanceId']
    InstanceId = message['InstanceId']
    PrivateDnsName = message['PrivateDnsName']

    try: 
        gamelift.update_game_server(
            GameServerGroupName=GameServerGroupName,
            GameServerId=GameServerId,
            HealthCheck='HEALTHY'
        )
        print_messageId(f'Updated Gamelift game server {GameServerId} health', flush=True)
    except gamelift.exceptions.NotFoundException as e:
        print_messageId(f'Skipping healthcheck, the node {GameServerId} is not registered', flush=True)
    
    # Adding taint ACTIVE to node
    taint = {
        "key": "gamelift.status/active",
        "value": "true",
        "effect": "NoExecute"
    }
    kubernetes_tools.taint_node(GameServerId, PrivateDnsName, json.dumps(taint))
    pass

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

def get_health_status(message):
    """This is another asynchronous method that checks the health of the viability of the instance according to FleetIQ.
    It is subscribing to a Redis channel for updates because the DescribeGameServerInstances API has a low throttling rate.
    For this to work, a separate application has to be deployed onto the cluster.  This application gets the viability of 
    each instance and publishes it on a separate channel for each instance.  When the viability changes to DRAINING, the node
    is cordoned and tainted, preventing Agones from scheduling new game servers on the instance.  Game servers in the non-Allocated
    state will be rescheduled onto other instances."""

    status = message['InstanceStatus']
    GameServerGroupName = message['GameServerGroupName']
    GameServerId = message['InstanceId']
    InstanceId = message['InstanceId']
    PrivateDnsName = message['PrivateDnsName']

    if status == 'ACTIVE':
        return
    global r
    is_cordoned = (True, False)[r.hget(name="{InstanceId}", key="is_cordoned") == 1]
    is_ready_shutdown = (True, False)[r.hget(name="{InstanceId}", key="is_cordoned") == 1]
    is_waiting_for_termination = (True, False)[r.hget(name="{InstanceId}", key="is_cordoned") == 1]

    # is_cordoned = False
    # is_ready_shutdown = False
    # is_waiting_for_termination = False

    print_messageId('Starting message loop', flush=True)
    print_messageId(f'is_cordoned: {is_cordoned}, is_ready_shutdown: {is_ready_shutdown}, is_waiting_for_termination: {is_waiting_for_termination}', flush=True)
    print_messageId(f"Instance {GameServerId}/{PrivateDnsName} status is: {status}", flush=True)

    try:
        if status == 'DRAINING':
            print_messageId(f'Instance is no longer viable', flush=True)
            is_cordoned = cordon_and_protect(PrivateDnsName, GameServerId)
            is_cordoned = True
            r.hset(name="{InstanceId}", key="is_cordoned", value=(1, 0)[is_cordoned])

            print_messageId(f'Instance should have no allocted session', flush=True)
            is_ready_shutdown = is_ready_shutdown_check(PrivateDnsName)
            r.hset(name="{InstanceId}", key="is_ready_shutdown", value=(1, 0)[is_ready_shutdown])

            print_messageId(f'Instance should be deregistered', flush=True)
            deregister_game_server(GameServerGroupName, GameServerId)
            is_waiting_for_termination = True
            r.hset(name="{InstanceId}", key="is_waiting_for_termination", value=(1, 0)[is_waiting_for_termination])
            
            if is_waiting_for_termination == True:
                print_messageId(f'Waiting for termination signal', flush=True)
                
        elif status == 'SPOT_TERMINATING':
            if is_waiting_for_termination == True:
                # This is never invoked because the status never equals SPOT_TERMINATING  
                print_messageId(f'Received termination signal', flush=True)
                kubernetes_tools.drain_pods(InstanceId, PrivateDnsName)  
        else:
            pass     

    except Exception as e:
        print_messageId(f'get_health_status->error: {e}', flush=True)   

    # try:
    #     if is_waiting_for_termination == True and status == 'DRAINING':
    #         print_messageId(f'Waiting for termination signal', flush=True)
    #     elif is_waiting_for_termination == True and status == 'SPOT_TERMINATING':
    #         # This is never invoked because the status never equals SPOT_TERMINATING  
    #         print_messageId(f'Received termination signal', flush=True)
    #         kubernetes_tools.drain_pods(InstanceId, PrivateDnsName)
    #         pass
    #     elif is_ready_shutdown == True:
    #         is_waiting_for_termination = deregister_game_server(GameServerGroupName, GameServerId)
    #         #r.hset(name="{InstanceId}", key="is_waiting_for_termination", value=(1, 0)[is_waiting_for_termination])
    #         pass
    #     elif status == 'DRAINING' and is_cordoned == True:
    #         # This seems to be a block call.  Delays the main loop, get_health_status, until resolved.
    #         # I think I neeed to spawn a new thread here. 
    #         is_ready_shutdown = is_ready_shutdown_check(PrivateDnsName)
    #         #r.hset(name="{InstanceId}", key="is_ready_shutdown", value=(1, 0)[is_ready_shutdown])
    #         pass
    #     elif status == 'DRAINING' and is_cordoned == False:
    #         print_messageId(f'Instance is no longer viable', flush=True)
    #         is_cordoned = cordon_and_protect(PrivateDnsName, GameServerId)
    #         #r.hset(name="{InstanceId}", key="is_cordoned", value=(1, 0)[is_cordoned])
    #         pass
    #     else :
    #         pass
    # except Exception as e:
    #     print_messageId(f'get_health_status->error: {e}', flush=True)

    print_messageId(f'is_cordoned: {is_cordoned}, is_ready_shutdown: {is_ready_shutdown}, is_waiting_for_termination: {is_waiting_for_termination}', flush=True)
    print_messageId(f'Finished get health status loop', flush=True)
    pass

def termination_handler(GameServerGroupName: str, GameServerId: str):
    """This method calls the drain pods method to evict non-essential pods from the node
    and waits to receive the termination signal from EC2 metadata."""
    # This method is never called because the instance never receives a termination signal
    print_messageId(f'Shutting down', flush=True)
    # Drain pods from the instance

    pass

r = None
def main_loop(messageId, message):
    set_messageId(messageId, message['InstanceId'])
    print_messageId(message)
    global r
    r = redis_conn()
    rkey = '{}.{}'.format(message['GameServerGroupName'], message['InstanceId'])
    if r.setnx(rkey, '1') == True:
        print_messageId(f'access {rkey}')
        initialize_game_server(message)
    
    update_health_status(message)
    get_health_status(message)
    pass

def lambda_handler(event, context):
    print(f'ALL START!!!', flush=True) 

    for record in event['Records']:
        for message in json.loads(record['body']):
            main_loop(record["messageId"], message)
    
    print(f'ALL DONE!!!', flush=True) 
    # TODO implement
    return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda!')
    }
