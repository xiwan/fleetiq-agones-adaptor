import json
from kubernetes import client, config
from kubernetes.client.rest import ApiException


#SERVICE_ACCOUNT=cluster-autoscaler
#SECRET=$(kubectl get serviceaccount -n kube-system ${SERVICE_ACCOUNT} -o json | jq -Mr '.secrets[].name | select(contains("token"))')  
#TOKEN=$(kubectl get secret -n kube-system ${SECRET} -o json | jq -Mr '.data.token' | base64 -d)
#kubectl get secret -n kube-system ${SECRET} -o json | jq -Mr '.data["ca.crt"]' | base64 -d > /tmp/ca.crt  
#APISERVER=$(kubectl config view --minify | grep server | cut -f 2- -d ":" | tr -d " ")

kube_url = 'https://ABCA2B1245BBC59EA52140FB2F039017.gr7.us-east-1.eks.amazonaws.com'
kube_token = 'eyJhbGciOiJSUzI1NiIsImtpZCI6IkFTX1BJcFljbE5QOGZwRF9IX3ZHWG03NUV6Vk5ZVERwNWE4OG1jR2dWNHMifQ.eyJpc3MiOiJrdWJlcm5ldGVzL3NlcnZpY2VhY2NvdW50Iiwia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9uYW1lc3BhY2UiOiJrdWJlLXN5c3RlbSIsImt1YmVybmV0ZXMuaW8vc2VydmljZWFjY291bnQvc2VjcmV0Lm5hbWUiOiJjbHVzdGVyLWF1dG9zY2FsZXItdG9rZW4tcDZyc3MiLCJrdWJlcm5ldGVzLmlvL3NlcnZpY2VhY2NvdW50L3NlcnZpY2UtYWNjb3VudC5uYW1lIjoiY2x1c3Rlci1hdXRvc2NhbGVyIiwia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9zZXJ2aWNlLWFjY291bnQudWlkIjoiYTA2Y2Q2NjktNzA1Ny00NjA4LTgxOWQtZDllNjhhZTUyYzk2Iiwic3ViIjoic3lzdGVtOnNlcnZpY2VhY2NvdW50Omt1YmUtc3lzdGVtOmNsdXN0ZXItYXV0b3NjYWxlciJ9.RYWcEEYknMn5aEqyfW8i9zGjqtGKz5ipRhUf8BdlIEy3wIWot7oWmpSf2_rKStp92lTQ-_BwmkGJcM4n-SCaewK1tVBdKJvfffrNbfHX2aAEOjUDxFMBryqTzOPNy7Ie5A0EtRSAkMOQApuCJ4JYUY6Ez6kA5hDMOcW8nEPWE2BGG-T4oTcfbSVB3FZpnsIa_fdGd1psUCiUyKUFnqt8iaXCGv5pkFYOm5H5ufyOa9j2wET0OXpUAOedzw_EtT1KNcO6psVMN2v8xVK2_oIlOyDTtwvSP0TbF7z1fqLxCTxzUdM5JOo4znFFxom1TWNjy1kBcAq8Wp_B571gU7Zd9g'
#kube_token = 'eyJhbGciOiJSUzI1NiIsImtpZCI6IkFTX1BJcFljbE5QOGZwRF9IX3ZHWG03NUV6Vk5ZVERwNWE4OG1jR2dWNHMifQ.eyJpc3MiOiJrdWJlcm5ldGVzL3NlcnZpY2VhY2NvdW50Iiwia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9uYW1lc3BhY2UiOiJkZWZhdWx0Iiwia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9zZWNyZXQubmFtZSI6ImRlZmF1bHQtdG9rZW4tcW1wZzkiLCJrdWJlcm5ldGVzLmlvL3NlcnZpY2VhY2NvdW50L3NlcnZpY2UtYWNjb3VudC5uYW1lIjoiZGVmYXVsdCIsImt1YmVybmV0ZXMuaW8vc2VydmljZWFjY291bnQvc2VydmljZS1hY2NvdW50LnVpZCI6IjMwYjE3NTE2LTkxMjYtNDJiYi04ZDVjLWU0Yzc1OTQ4ZWFhNyIsInN1YiI6InN5c3RlbTpzZXJ2aWNlYWNjb3VudDpkZWZhdWx0OmRlZmF1bHQifQ.JO052J2QcFbI7pyGVvJIR2KPfff3bh_yhXfTeSxOF4NnDVkW1E1fLshe6kTNDj1CeXToAYxkFF16zZooZurHs0OGNjyLQiqqLwdN_J7fTxEYneZe2NDE9_hOQgxOQSFlY9Yt_npsPv399p4cRljedp5ei05dSY9FXzKllag0YHusvhG-OiOw6PH5o7O9gbrupNsUjNqegA-BvWMjiPBILs-sNUhaM8c5nK2WWLjQX9oG737Vlf0TA_GxBWzuJfSz2xYW7om9gDaYnKu3qNWiG1Ds_-E1zlgjOthjs-WsIBeI-VDmgiP6MMwTH_9i6-jkNT0WHAYcdla13bCajtD_jA'
class KubernetesTools(object):
    def __init__(self):
        configuration = client.Configuration()
        configuration.host = kube_url
        configuration.verify_ssl = False
        configuration.api_key = {"authorization": "Bearer " + kube_token}
        client1 = client.ApiClient(configuration=configuration)
        self.core_v1_client = client.CoreV1Api(client1)
        self.custom_obj_client = client.CustomObjectsApi(client1)
        self.grace_period = 30

    def set_messageId(self, messageId: str, instanceId: str):
        self.messageId = messageId
        self.instanceId = instanceId

    def print_messageId(self, Content : str, flush : bool = True):
        print(f'[{self.messageId}|{self.instanceId}] : {Content}', flush=flush)
        pass

    def taint_pod(self, PodName: str, PodNamespace: str, Toleration: str):
        toleration = json.loads(Toleration)
        print_messageId(f"Updating {PodNamespace}/{PodName} with toleration", flush=True)
        # Patch game server pod with toleration for DRAINING
        try:
            pods = self.core_v1_client.read_namespaced_pod(name=PodName, namespace=PodNamespace)
        except ApiException as e:
            print_messageId(f'Exception when calling CoreV1Api->read_namespaced_pod: {e}\n', flush=True)
            return False
        # if pods is None:
        #     return
        # tolerations check
        tolerations = pods.spec.tolerations
        if tolerations is None:
            print_messageId(f'tolerations is none', flush=True)
            tolerations = []
        tolerations.append(toleration)
        toleration_body = {
            "spec": {
                "tolerations": tolerations
            }
        }

        try:
            self.core_v1_client.patch_namespaced_pod(name=PodName, namespace=PodNamespace, body=toleration_body)
        except ApiException as e:
            print_messageId(f'Exception when calling CoreV1Api->patch_namespaced_pod: {e}\n', flush=True)
        pass
        return True

    def drain_pods(self, InstanceId: str, PrivateDnsName: str,):
        """This method evicts all Kubernetes pods from the specified node that are not in the kube-system namespace."""
        field_selector = 'spec.nodeName=' + PrivateDnsName
        try:
            pods = self.core_v1_client.list_pod_for_all_namespaces(watch=False, field_selector=field_selector)
        except ApiException as e:
            print(f'Exception when calling CoreV1Api->list_pod_for_all_namespaces: {e}\n', flush=True)
            # return
        # Create a filtered list of pods not in the kube-system namespace
        filtered_pods = filter(lambda x: x.metadata.namespace != 'kube-system', pods.items)
        for pod in filtered_pods:
            print_messageId(f'Deleting pod {pod.metadata.name} in namespace {pod.metadata.namespace}', flush=True)

            if self.grace_period > 0:
                body = {
                    'apiVersion': 'policy/v1beta1',
                    'kind': 'Eviction',
                    'metadata': {
                        'name': pod.metadata.name,
                        'namespace': pod.metadata.namespace,
                        'grace_period_seconds': self.grace_period
                    }
                }
            else:
                body = {
                    'apiVersion': 'policy/v1beta1',
                    'kind': 'Eviction',
                    'metadata': {
                        'name': pod.metadata.name,
                        'namespace': pod.metadata.namespace
                    }
                }
            try:
                self.core_v1_client.create_namespaced_pod_eviction(pod.metadata.name, pod.metadata.namespace, body)
            except ApiException as e:
                print_messageId(f'Exception when calling CoreV1Api->create_namespaced_pod_eviction: {e}\n', flush=True)
        pass

    def taint_node(self, InstanceId: str, PrivateDnsName: str, TaintCont: str):
        """
        This method taint the node with specific key value and effect combination to mark the node
        is active and acclaimed, not available for new pod any more
        """
        # Adding taint to node
        taint = json.loads(TaintCont)
        try: 
            node = self.core_v1_client.read_node(PrivateDnsName)
        except ApiException as e:
            print_messageId(f'Exception when calling CoreV1Api->read_node: {e}\n', flush=True)
            return False
        # taints check
        if node.spec is None:
            return False
        taints = node.spec.taints
        if taints is None:
            print_messageId(f'taints is none', flush=True)
            taints = []      
        taints.append(taint)
        taint_body = {
            "spec": {
                "taints": taints
            }
        }
        try:
            self.core_v1_client.patch_node(PrivateDnsName, taint_body)
            print_messageId(f'Node {InstanceId} has been tainted', flush=True)
        except ApiException as e:
            print_messageId(f'The node {InstanceId} has already been tainted', flush=True)
        return True

    def patch_node(self, InstanceId: str, PrivateDnsName: str, Cordon: str):
        try:
            self.core_v1_client.patch_node(PrivateDnsName, json.loads(Cordon))
            print_messageId(f'Node {InstanceId} has been cordoned', flush=True)
        except ApiException as e:
            print_messageId(f'Exception when calling CoreV1Api->patch_node: {e}\n', flush=True)
        pass

    def get_game_servers(self, PrivateDnsName: str):
        """This is an asynchronous method call that checks to see whether there are any Agones game servers in the Allocated state.
        It runs once per minute and will continue running until there are no more Allocated game servers in the instance. So long as there are
        Allocated game servers, the instance is protected from scale-in by the ASG and cluster-autoscaler."""
        print_messageId('Scanning instance for game servers', flush=True)
        custom_objs = None
        try:
            custom_objs = self.custom_obj_client.list_cluster_custom_object(group='agones.dev', version='v1', plural='gameservers')
        except ApiException as e:
            print_messageId(f'Exception when calling CustomObjectsApi->list_cluster_custom_object: {e}')

        if custom_objs is None:
            return []
        game_servers = custom_objs['items']
        return list(filter(lambda x: x['status']['state']=='Allocated' and x['status']['nodeName']==PrivateDnsName, game_servers))
        

#print("Listing pods with their IPs:")
#ret = kubernetes_tools.get_api().list_pod_for_all_namespaces(watch=False)
#for i in ret.items:
#    print("%s\t%s\t%s" % (i.status.pod_ip, i.metadata.namespace, i.metadata.name))

kubernetes_tools = KubernetesTools()
print_messageId = kubernetes_tools.print_messageId