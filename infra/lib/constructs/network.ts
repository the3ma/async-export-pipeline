import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';

export interface NetworkProps {
  /** Import this VPC instead of creating one (needs an explicit account/region on the stack). */
  readonly vpcId?: string;
  /** Where the PostgreSQL database lives. */
  readonly dbCidr: string;
  readonly dbPort: number;
}

/**
 * Optional private networking for the Lambdas.
 *
 * The Lambda security group has NO default egress: only the database port to `dbCidr`, and 443 to the
 * interface endpoints' security group. Every AWS API the functions call (S3, DynamoDB, SQS, Secrets
 * Manager, SES) is reached through an interface endpoint, so no NAT gateway and no internet route are needed.
 */
export class Network extends Construct {
  public readonly vpc: ec2.IVpc;
  public readonly subnets: ec2.SubnetSelection;
  public readonly lambdaSecurityGroup: ec2.SecurityGroup;
  public readonly endpointSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: NetworkProps) {
    super(scope, id);

    if (props.vpcId) {
      this.vpc = ec2.Vpc.fromLookup(this, 'Vpc', { vpcId: props.vpcId });
      this.subnets = { subnets: this.vpc.privateSubnets.length > 0 ? this.vpc.privateSubnets : this.vpc.isolatedSubnets };
    } else {
      const vpc = new ec2.Vpc(this, 'Vpc', {
        maxAzs: 2,
        natGateways: 0,
        subnetConfiguration: [{ name: 'isolated', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 }],
        flowLogs: { rejected: { trafficType: ec2.FlowLogTrafficType.REJECT } },
      });
      this.vpc = vpc;
      this.subnets = { subnetType: ec2.SubnetType.PRIVATE_ISOLATED };
    }

    this.endpointSecurityGroup = new ec2.SecurityGroup(this, 'EndpointSg', {
      vpc: this.vpc,
      description: 'Interface VPC endpoints used by the export pipeline Lambdas',
      allowAllOutbound: false,
    });

    this.lambdaSecurityGroup = new ec2.SecurityGroup(this, 'LambdaSg', {
      vpc: this.vpc,
      description: 'Export pipeline Lambdas: database port + HTTPS to VPC endpoints only',
      allowAllOutbound: false,
    });
    this.lambdaSecurityGroup.addEgressRule(ec2.Peer.ipv4(props.dbCidr), ec2.Port.tcp(props.dbPort), 'PostgreSQL');
    this.lambdaSecurityGroup.addEgressRule(this.endpointSecurityGroup, ec2.Port.tcp(443), 'AWS APIs via interface endpoints');
    this.endpointSecurityGroup.addIngressRule(this.lambdaSecurityGroup, ec2.Port.tcp(443), 'HTTPS from export pipeline Lambdas');

    const endpoints: Record<string, ec2.InterfaceVpcEndpointAwsService> = {
      Sqs: ec2.InterfaceVpcEndpointAwsService.SQS,
      SecretsManager: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
      Ses: ec2.InterfaceVpcEndpointAwsService.EMAIL,
      S3: ec2.InterfaceVpcEndpointAwsService.S3,
      DynamoDb: ec2.InterfaceVpcEndpointAwsService.DYNAMODB,
    };
    for (const [name, service] of Object.entries(endpoints)) {
      const dualMode = name === 'S3' || name === 'DynamoDb';
      this.vpc.addInterfaceEndpoint(`${name}Endpoint`, {
        service,
        subnets: this.subnets,
        securityGroups: [this.endpointSecurityGroup],
        privateDnsEnabled: true,
        open: false,
        // S3 / DynamoDB also offer gateway endpoints; resolve their public names to the interface endpoint.
        ...(dualMode
          ? { privateDnsOnlyForInboundResolverEndpoint: ec2.VpcEndpointPrivateDnsOnlyForInboundResolverEndpoint.ALL_RESOLVERS }
          : {}),
      });
    }
  }
}
