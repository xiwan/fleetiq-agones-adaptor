import json, os, time
import boto3
import logging
import random

logging.basicConfig(level=logging.INFO)

sqs = boto3.resource('sqs')
ec2 = boto3.client('ec2', region_name=os.getenv('AWS_REGION'))
sqsName = 'message.fifo'

    
def decribe_instance(game_server):
    print(f'{game_server["InstanceId"]}:{game_server["InstanceStatus"]}', flush=True)
    Reservations = ec2.describe_instances(InstanceIds=[game_server["InstanceId"]]).get("Reservations")
    #print(json.dumps(Reservations), flush=True)
    for reservation in Reservations:
        for instance in reservation['Instances']:
            game_server["PrivateDnsName"] = instance.get("PrivateDnsName")
            game_server["PrivateIpAddress"] = instance.get("PrivateIpAddress")
            game_server["PublicDnsName"] = instance.get("PublicDnsName")
            game_server["PublicIpAddress"] = instance.get("PublicIpAddress") 
            
    return game_server

"""
    Current status of the game server instance.

        ACTIVE -- The instance is viable for hosting game servers.
        DRAINING -- The instance is not viable for hosting game servers. Existing game servers are in the process of ending, and new game servers are not started on this instance unless no other resources are available. When the instance is put in DRAINING, a new instance is started up to replace it. Once the instance has no UTILIZED game servers, it will be terminated in favor of the new instance.
        SPOT_TERMINATING -- The instance is in the process of shutting down due to a Spot instance interruption. No new game servers are started on this instance.
"""          
def lambda_handler(event, context):
 
    queue = sqs.get_queue_by_name(QueueName=sqsName)
    
    gamelift = boto3.client('gamelift', region_name=os.getenv('AWS_REGION'))
    paginator = gamelift.get_paginator('describe_game_server_instances')
    #TODO get GAME_SERVER_GROUP_NAME from a ConfigMap and loop through the values
    groups = json.loads(os.getenv('CONFIG_TXT'))
    for group in groups['GameServerGroups']:
        pages = paginator.paginate(GameServerGroupName=group)
        for page in pages:
            game_servers = []
            for game_server in page['GameServerInstances']:
                
                game_server = decribe_instance(game_server)
                if 'PrivateDnsName' in game_server and len(game_server['PrivateDnsName']) > 0:
                    print(f'Publishing status on channel {game_server["InstanceId"]}', flush=True)
                    game_servers.append(game_server)

            if len(game_servers) == 0:
                continue
            randomsuffix = str(random.randint(1,1000))
            response = queue.send_message(
                MessageBody=json.dumps(game_servers), 
                MessageGroupId=group, 
                MessageDeduplicationId=group + randomsuffix)
                
            print(f'response = {response}')        
    return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda!')
    }
