import * as path from 'node:path';
import type { Node } from 'constructs';

export type Stage = 'dev' | 'prod';

/**
 * Everything the stack needs, resolved from CDK context (`-c key=value` or cdk.json).
 * Keeping the parsing in one place makes the stack itself declarative and testable.
 */
export interface PipelineConfig {
  readonly stage: Stage;
  /** Absolute path of the repository `services/` directory (Lambda source). */
  readonly servicesDir: string;
  /** Pre-built worker zip (CI "build once, deploy the artifact"). */
  readonly workerArtifact?: string;
  /** Pre-built api zip. */
  readonly apiArtifact?: string;
  /** Copy sources without dependencies instead of bundling in Docker (synth/test only). */
  readonly skipBundling: boolean;
  readonly workerHandler: string;
  readonly apiHandler: string;
  readonly workerReservedConcurrency: number;
  readonly workerMemoryMb: number;
  readonly workerTimeoutMinutes: number;
  readonly partRows: number;
  readonly maxParts: number;
  readonly presignSeconds: number;
  readonly mailFrom: string;
  /** Demo recipient mapping used by the worker: <owner_id>@<mailToDomain>. */
  readonly mailToDomain: string;
  /** Browser origin of the web UI, passed to the api as CORS_ORIGINS. */
  readonly webOrigin?: string;
  readonly appBaseUrl: string;
  readonly alarmEmail?: string;
  /** Existing Secrets Manager secret holding the worker (read-only `exporter` role) DB connection. */
  readonly workerDbSecretArn?: string;
  /** Existing Secrets Manager secret holding the api (`app` role) DB connection. */
  readonly apiDbSecretArn?: string;
  readonly useVpc: boolean;
  /** Import an existing VPC (requires an explicit account/region env). Otherwise a VPC is created. */
  readonly vpcId?: string;
  readonly dbCidr: string;
  readonly dbPort: number;
  readonly jwtIssuer?: string;
  readonly jwtAudience?: string[];
  readonly retainData: boolean;
  readonly pointInTimeRecovery: boolean;
  readonly logRetentionDays: number;
}

function str(node: Node, key: string): string | undefined {
  const v: unknown = node.tryGetContext(key);
  if (v === undefined || v === null || v === '') return undefined;
  return String(v);
}

function bool(node: Node, key: string, fallback: boolean): boolean {
  const v = str(node, key);
  if (v === undefined) return fallback;
  return ['true', '1', 'yes'].includes(v.toLowerCase());
}

function int(node: Node, key: string, fallback: number, min: number, max: number): number {
  const v = str(node, key);
  if (v === undefined) return fallback;
  const n = Number(v);
  if (!Number.isInteger(n) || n < min || n > max) {
    throw new Error(`Context "${key}" must be an integer in [${min}, ${max}], got "${v}"`);
  }
  return n;
}

function absolute(p: string | undefined): string | undefined {
  return p === undefined ? undefined : path.resolve(p);
}

export function loadConfig(node: Node): PipelineConfig {
  const stageRaw = str(node, 'stage') ?? 'dev';
  if (stageRaw !== 'dev' && stageRaw !== 'prod') {
    throw new Error(`Context "stage" must be "dev" or "prod", got "${stageRaw}"`);
  }
  const stage: Stage = stageRaw;
  const prod = stage === 'prod';

  const audience = str(node, 'jwtAudience');

  return {
    stage,
    servicesDir: absolute(str(node, 'servicesDir')) ?? path.resolve(__dirname, '..', '..', 'services'),
    workerArtifact: absolute(str(node, 'workerArtifact')),
    apiArtifact: absolute(str(node, 'apiArtifact')),
    skipBundling: bool(node, 'skipBundling', false),
    workerHandler: str(node, 'workerHandler') ?? 'export_worker.handler.handler',
    apiHandler: str(node, 'apiHandler') ?? 'export_api.lambda_handler.handler',
    workerReservedConcurrency: int(node, 'workerReservedConcurrency', 5, 1, 1000),
    workerMemoryMb: int(node, 'workerMemoryMb', 2048, 512, 10240),
    workerTimeoutMinutes: int(node, 'workerTimeoutMinutes', 15, 1, 15),
    partRows: int(node, 'partRows', 1_000_000, 1, 10_000_000),
    maxParts: int(node, 'maxParts', 20, 1, 99),
    presignSeconds: int(node, 'presignSeconds', 60, 1, 3600),
    mailFrom: str(node, 'mailFrom') ?? 'exports@example.com',
    mailToDomain: str(node, 'mailToDomain') ?? 'example.com',
    webOrigin: str(node, 'webOrigin'),
    appBaseUrl: str(node, 'appBaseUrl') ?? 'https://exports.example.com',
    alarmEmail: str(node, 'alarmEmail'),
    workerDbSecretArn: str(node, 'workerDbSecretArn'),
    apiDbSecretArn: str(node, 'apiDbSecretArn'),
    useVpc: bool(node, 'useVpc', false),
    vpcId: str(node, 'vpcId'),
    dbCidr: str(node, 'dbCidr') ?? '10.0.0.0/16',
    dbPort: int(node, 'dbPort', 5432, 1, 65535),
    jwtIssuer: str(node, 'jwtIssuer'),
    jwtAudience: audience?.split(',').map((a) => a.trim()).filter(Boolean),
    retainData: prod,
    pointInTimeRecovery: prod,
    logRetentionDays: prod ? 90 : 14,
  };
}
