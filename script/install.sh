#!/bin/sh

# 安装jq
sudo yum -y install jq

# 取ACCOUNT_ID 和 AWS_REGION
export ACCOUNT_ID=$(aws sts get-caller-identity --output text --query Account)
export AWS_REGION=$(curl -s 169.254.169.254/latest/dynamic/instance-identity/document | jq -r '.region')

# 验证环境变量
test -n "$AWS_REGION" && echo AWS_REGION is "$AWS_REGION" || echo AWS_REGION is not set
aws sts get-caller-identity --query Arn | grep agonesfleetiq-admin -q && echo "IAM role valid" || echo "IAM role NOT valid"

# 安装工具
# 安装kubectl（这里用的1.18，可以根据需求用到更新的版本）
sudo curl --silent --location -o /usr/local/bin/kubectl \https://amazon-eks.s3.us-west-2.amazonaws.com/1.18.9/2020-11-02/bin/linux/amd64/kubectl
sudo chmod +x /usr/local/bin/kubectl
# 安装AWS CLI
sudo pip install --upgrade awscli && hash -r
# 安装工具集
sudo yum -y install gettext bash-completion moreutils
echo 'yq() {
  docker run --rm -i -v "${PWD}":/workdir mikefarah/yq yq "$@"
  }' | tee -a ~/.bashrc && source ~/.bashrc
# 验证
for command in kubectl jq envsubst aws
do
which $command &>/dev/null && echo "$command in path" || echo "$command NOT FOUND"
done

# 安装eksctl
curl --silent --location "https://github.com/weaveworks/eksctl/releases/latest/download/eksctl_$(uname -s)_amd64.tar.gz" | tar xz -C /tmp
sudo mv -v /tmp/eksctl /usr/local/bin

# 创建可以获取FleetIQ节点状态的IAM角色
aws iam create-policy --policy-name FleetIQpermissionsEC2 --policy-document '{"Version": "2012-10-17","Statement": [{"Sid": "VisualEditor0","Effect": "Allow","Action": ["gamelift:DescribeGameServerGroup","gamelift:DescribeGameServerInstances","gamelift:DescribeGameServer"],"Resource": "*"}]}'

# 配置和部署EKS 集群 (这里使用了两个非托管节点，可以根据需求调整)

cat <<EOF > config.yaml
---
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: agones
  region: ${AWS_REGION}
  version: "1.18"
availabilityZones: ["${AWS_REGION}a", "${AWS_REGION}b", "${AWS_REGION}c"]
nodeGroups:
  - name: ng-system
    instanceType: m5.large
    desiredCapacity: 2
    iam:
      attachPolicyARNs:
        - arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy
        - arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy
        - arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess
        - arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
        - arn:aws:iam::${ACCOUNT_ID}:policy/FleetIQpermissionsEC2
  - name: ng-agones
    instanceType: m5.xlarge
    desiredCapacity: 2
    labels:
      agones.dev/agones-system: "true"
    taints:
      agones.dev/agones-system: "true:NoExecute"
    iam:
      attachPolicyARNs:
        - arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy
        - arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy
        - arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess
        - arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
        - arn:aws:iam::${ACCOUNT_ID}:policy/FleetIQpermissionsEC2
EOF

eksctl create cluster -f config.yaml


# 配置节点组
# 生成启动模板的user data

NG_STACK=$(aws cloudformation describe-stacks --region ${AWS_REGION}| jq -r '.Stacks[] | .StackId' | grep ng-system)LAUNCH_TEMPLATE_ID=$(aws cloudformation describe-stack-resources --region ${AWS_REGION} --stack-name $NG_STACK \
| jq -r '.StackResources | map(select(.LogicalResourceId == "NodeGroupLaunchTemplate")
| .PhysicalResourceId)[0]')

aws ec2 describe-launch-template-versions --region ${AWS_REGION} --launch-template-id$LAUNCH_TEMPLATE_ID \
| jq -r '.LaunchTemplateVersions[0].LaunchTemplateData.UserData' \
| base64 -d | gunzip > launchtemplate.yaml

awk -v var="$(grep -n NODE_LABELS=alpha ./launchtemplate.yaml | head -1 | cut -d : -f 1)" 'NR==var {$0="    NODE_LABELS=alpha.eksctl.io/cluster-name=agones,alpha.eksctl.io/nodegroup-name=game-servers-1,role=game-servers-1"} 1' launchtemplate.yaml > templt.yaml
awk -v var="$(grep -n NODE_TAINTS= ./launchtemplate.yaml | head -1 | cut -d : -f 1)" 'NR==var {$0="    NODE_TAINTS=agones.dev/gameservers=true:NoExecute"} 1' templt.yaml >modlaunchtemplate.yaml
rm templt.yaml
base64 -w 0 modlaunchtemplate.yaml > b64modlaunchtemplate

# 创建启动模板
VPCID=$(aws ec2 describe-vpcs --region ${AWS_REGION} --filter Name=tag:alpha.eksctl.io/cluster-name,Values=agones | jq -r '.Vpcs[0].VpcId')
 
SGID=$(aws ec2 create-security-group --region ${AWS_REGION} --description "Agones nodegroup SG" --group-name eksctl-agones-nodegroup-ng-1-SG --vpc-id ${VPCID} | jq -r '.GroupId')
 
SGINGRESSRULES=$(aws ec2 describe-security-groups --region ${AWS_REGION} --filter Name=tag:alpha.eksctl.io/nodegroup-name,Values=ng-system | jq '.SecurityGroups[0].IpPermissions')
 
echo '{"GroupId":"'$SGID'","IpPermissions":'$SGINGRESSRULES'}' > sgingress.json
aws ec2 authorize-security-group-ingress --cli-input-json file://sgingress.json --region ${AWS_REGION}    
 
aws ec2 authorize-security-group-ingress --group-id ${SGID} --ip-permissions FromPort=7000,IpProtocol="udp",IpRanges=[{CidrIp="0.0.0.0/0"}],ToPort=8000 --region ${AWS_REGION}
 
IAMINSTANCEPROFILE=$(aws ec2 describe-launch-template-versions --region ${AWS_REGION} --launch-template-id $LAUNCH_TEMPLATE_ID | jq '.LaunchTemplateVersions[0].LaunchTemplateData.IamInstanceProfile.Arn')
 
NG0SG1=$(aws ec2 describe-launch-template-versions --region ${AWS_REGION} --launch-template-id$LAUNCH_TEMPLATE_ID | jq '.LaunchTemplateVersions[0].LaunchTemplateData.NetworkInterfaces[0].Groups[0]')
 
NG0SG2=$(aws ec2 describe-launch-template-versions --region ${AWS_REGION} --launch-template-id$LAUNCH_TEMPLATE_ID | jq '.LaunchTemplateVersions[0].LaunchTemplateData.NetworkInterfaces[0].Groups[1]')
 
NG0AMI=$(aws ec2 describe-launch-template-versions --region ${AWS_REGION} --launch-template-id$LAUNCH_TEMPLATE_ID | jq '.LaunchTemplateVersions[0].LaunchTemplateData.ImageId')
 
B64USERDATA=$(cat b64modlaunchtemplate)
 
cat << EOF > ltinput.json
{
"LaunchTemplateName": "eksctl-agones-nodegroup-ng-1",
"VersionDescription": "FleetIQ GameServerGroup LT",
"LaunchTemplateData": {
"EbsOptimized": true,
"IamInstanceProfile": {
"Arn": ${IAMINSTANCEPROFILE}
},
"BlockDeviceMappings": [
{
"DeviceName": "/dev/xvda",
"Ebs": {
"Encrypted": false,
"DeleteOnTermination": true,
"VolumeSize": 80,
"VolumeType": "gp2"
}
}
],
"NetworkInterfaces": [
{
"DeviceIndex": 0,
"Groups": [
"${NG0SG1}",
"${NG0SG2}",
"${SGID}"
]
}
],
"ImageId": $NG0AMI,
"Monitoring": {
"Enabled": true
},
"UserData": "${B64USERDATA}",
"TagSpecifications": [
{
"ResourceType": "instance",
"Tags": [
{
"Key": "kubernetes.io/cluster/agones",
"Value": "owned"
},
{
"Key": "k8s.io/cluster-autoscaler/agones",
"Value": "owned"
},
{
"Key": "k8s.io/cluster-autoscaler/enabled",
"Value": "true"
},
{
"Key": "Name",
"Value": "FleetIQ"
}
]
}
],
"MetadataOptions": {
"HttpTokens": "optional",
"HttpPutResponseHopLimit": 2
}
}
}
EOF
 
GSGLTID=$(aws ec2 create-launch-template --cli-input-json file://ltinput.json --region ${AWS_REGION} | jq '.LaunchTemplate.LaunchTemplateId')


# 创建让Gamelift可以创建和更新EC2 Auto Scaling Group的IAM 角色
GSGROLEARN=$(( aws iam create-role --role-name GameLiftServerGroupRole --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":["gamelift.amazonaws.com","autoscaling.amazonaws.com"]},"Action":"sts:AssumeRole"}]}' | jq '.Role.Arn' ) 2>&1)
 
aws iam attach-role-policy --role-name GameLiftServerGroupRole --policy-arn arn:aws:iam::aws:policy/GameLiftGameServerGroupPolicy


# 创建Gamelift的 Game Server Group
GSGNAME="agones-game-server-group-01"
PUBLICSUBNETIDS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPCID --region ${AWS_REGION} | jq '[.Subnets[] | {subnetid: .SubnetId, mapPublicIP: .MapPublicIpOnLaunch}]' | jq 'group_by(.mapPublicIP)' | jq '[.[1][].subnetid]')
 
cat << EOF > gsgconfig.json
{
"GameServerGroupName": "${GSGNAME}",
"RoleArn": ${GSGROLEARN},
"MinSize": 1,
"MaxSize": 10,
"LaunchTemplate": {
"LaunchTemplateId": ${GSGLTID},
"Version": "1"
},
"InstanceDefinitions": [
{
"InstanceType": "c5.xlarge",
"WeightedCapacity": "2"
},
{
"InstanceType": "c4.xlarge",
"WeightedCapacity": "1"
}
],
"BalancingStrategy": "SPOT_PREFERRED",
"GameServerProtectionPolicy": "FULL_PROTECTION",
"VpcSubnets": ${PUBLICSUBNETIDS}
}
EOF
 
aws gamelift create-game-server-group --region ${AWS_REGION} --cli-input-json file://gsgconfig.json (file:///gsgconfig.json)

# 检查Gamelift 的状态
aws gamelift describe-game-server-group --game-server-group-name ${GSGNAME} --region $AWS_REGION | jq '.GameServerGroup.Status'

# 给Auto Scaling Group打上标签，方便CA调用
aws autoscaling create-or-update-tags --tags ResourceId=gamelift-gameservergroup-${GSGNAME},ResourceType=auto-scaling-group,Key=kubernetes.io/cluster/agones,Value=owned,PropagateAtLaunch=true ResourceId=gamelift-gameservergroup-${GSGNAME},ResourceType=auto-scaling-group,Key=k8s.io/cluster-autoscaler/agones,Value=enabled,PropagateAtLaunch=true ResourceId=gamelift-gameservergroup-${GSGNAME},ResourceType=auto-scaling-group,Key=Name,Value=FleetIQ,PropagateAtLaunch=true ResourceId=gamelift-gameservergroup-${GSGNAME},ResourceType=auto-scaling-group,Key=k8s.io/cluster-autoscaler/enabled,Value=true,PropagateAtLaunch=true --region ${AWS_REGION}

# 创建OIDC端点
eksctl utils associate-iam-oidc-provider --cluster agones --approve

# 创建CA权限和service account
eksctl create iamserviceaccount --cluster agones --namespace kube-system --name cluster-autoscaler --attach-policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/cluster-autoscaler-policy --override-existing-serviceaccounts --approve
cat << EOF > capolicy.json
{
"Version": "2012-10-17",
"Statement": [
{
"Effect": "Allow",
"Action": [
"autoscaling:DescribeAutoScalingGroups",
"autoscaling:DescribeAutoScalingInstances",
"autoscaling:DescribeLaunchConfigurations",
"autoscaling:DescribeTags",
"autoscaling:SetDesiredCapacity",
"autoscaling:TerminateInstanceInAutoScalingGroup"
],
"Resource": "*"
}
]
}
EOF
 
aws iam create-policy --policy-name cluster-autoscaler-policy --policy-document file://capolicy.json
 
# 创建CA，需要使用前面的 *CSGNAME* 参数。由于*”gamelift-gameservergroup-” ** *前缀会被自动加上，所以这个Auto Scaling Group的名字必须传递给K8s 的CA 来使用
cat << EOF > camanifest.yaml
---
apiVersion: v1
kind: ServiceAccount
metadata:
  labels:
    k8s-addon: cluster-autoscaler.addons.k8s.io
    k8s-app: cluster-autoscaler
  name: cluster-autoscaler
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: cluster-autoscaler
  labels:
    k8s-addon: cluster-autoscaler.addons.k8s.io
    k8s-app: cluster-autoscaler
rules:
  - apiGroups: [""]
    resources: ["events", "endpoints"]
    verbs: ["create", "patch"]
  - apiGroups: [""]
    resources: ["pods/eviction"]
    verbs: ["create"]
  - apiGroups: [""]
    resources: ["pods/status"]
    verbs: ["update"]
  - apiGroups: [""]
    resources: ["endpoints"]
    resourceNames: ["cluster-autoscaler"]
    verbs: ["get", "update"]
  - apiGroups: [""]
    resources: ["nodes", "gameservers"]
    verbs: ["watch", "list", "get", "update", "patch"]
  - apiGroups: [""]
    resources:
      - "pods"
      - "services"
      - "replicationcontrollers"
      - "persistentvolumeclaims"
      - "persistentvolumes"
    verbs: ["watch", "list", "get"]
  - apiGroups: ["extensions"]
    resources: ["replicasets", "daemonsets"]
    verbs: ["watch", "list", "get"]
  - apiGroups: ["policy"]
    resources: ["poddisruptionbudgets"]
    verbs: ["watch", "list"]
  - apiGroups: ["apps"]
    resources: ["statefulsets", "replicasets", "daemonsets"]
    verbs: ["watch", "list", "get"]
  - apiGroups: ["storage.k8s.io"]
    resources: ["storageclasses", "csinodes"]
    verbs: ["watch", "list", "get"]
  - apiGroups: ["batch", "extensions"]
    resources: ["jobs"]
    verbs: ["get", "list", "watch", "patch"]
  - apiGroups: ["coordination.k8s.io"]
    resources: ["leases"]
    verbs: ["create"]
  - apiGroups: ["coordination.k8s.io"]
    resourceNames: ["cluster-autoscaler"]
    resources: ["leases"]
    verbs: ["get", "update"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: cluster-autoscaler
  namespace: kube-system
  labels:
    k8s-addon: cluster-autoscaler.addons.k8s.io
    k8s-app: cluster-autoscaler
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["create","list","watch"]
  - apiGroups: [""]
    resources: ["configmaps"]
    resourceNames: ["cluster-autoscaler-status", "cluster-autoscaler-priority-expander"]
    verbs: ["delete", "get", "update", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: cluster-autoscaler
  labels:
    k8s-addon: cluster-autoscaler.addons.k8s.io
    k8s-app: cluster-autoscaler
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-autoscaler
subjects:
  - kind: ServiceAccount
    name: cluster-autoscaler
    namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: cluster-autoscaler
  namespace: kube-system
  labels:
    k8s-addon: cluster-autoscaler.addons.k8s.io
    k8s-app: cluster-autoscaler
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: cluster-autoscaler
subjects:
  - kind: ServiceAccount
    name: cluster-autoscaler
    namespace: kube-system
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cluster-autoscaler
  namespace: kube-system
  labels:
    app: cluster-autoscaler
  annotations:
    cluster-autoscaler.kubernetes.io/safe-to-evict: 'false'
spec:
  replicas: 1
  selector:
    matchLabels:
      app: cluster-autoscaler
  template:
    metadata:
      labels:
        app: cluster-autoscaler
      annotations:
        prometheus.io/scrape: 'true'
        prometheus.io/port: '8085'
    spec:
      serviceAccountName: cluster-autoscaler
      containers:
        - image: k8s.gcr.io/autoscaling/cluster-autoscaler:v1.18.3
          name: cluster-autoscaler
          resources:
            limits:
              cpu: 100m
              memory: 300Mi
            requests:
              cpu: 100m
              memory: 300Mi
          command:
            - ./cluster-autoscaler
            - --v=4
            - --stderrthreshold=info
            - --cloud-provider=aws
            - --skip-nodes-with-local-storage=false
            - --expander=priority
            - --nodes=0:10:gamelift-gameservergroup-${GSGNAME}
            - --balance-similar-node-groups
            - --skip-nodes-with-system-pods=false
          env:
            - name: AWS_REGION
              value: ${AWS_REGION}
          volumeMounts:
            - name: ssl-certs
              mountPath: /etc/ssl/certs/ca-certificates.crt
              readOnly: true
          imagePullPolicy: "Always"
      volumes:
        - name: ssl-certs
          hostPath:
            path: "/etc/ssl/certs/ca-bundle.crt"
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-autoscaler-priority-expander
  namespace: kube-system
data:
  priorities: |-
    10:
      - .*-non-existing-entry.*
    20:
      - gamelift-gameservergroup-${GSGNAME}
EOF

kubectl apply -f camanifest.yaml








