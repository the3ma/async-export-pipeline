import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { spawnSync } from 'node:child_process';
import type { BundlingOptions, ILocalBundling } from 'aws-cdk-lib';
import { DockerImage, IgnoreMode } from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';

/** Which Python component of `services/` a Lambda is built from (always together with `shared`). */
export type ServiceComponent = 'worker' | 'api';

/** How the code was resolved, so the stack can annotate non-deployable synth-only output. */
export type CodeSource = 'artifact' | 'docker' | 'source-copy';

export interface PythonCodeOptions {
  readonly component: ServiceComponent;
  readonly servicesDir: string;
  /** Pre-built deployment zip. Wins over everything else. */
  readonly artifact?: string;
  /** Force the dependency-free source copy (synth / unit tests only). */
  readonly skipBundling: boolean;
}

export interface ResolvedCode {
  readonly code: lambda.Code;
  readonly source: CodeSource;
}

const IGNORED = new Set(['.venv', 'venv', '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache', 'tests', 'test', 'node_modules', 'dist', 'build']);

const ASSET_EXCLUDES = [
  '**/.venv',
  '**/venv',
  '**/__pycache__',
  '**/.pytest_cache',
  '**/.ruff_cache',
  '**/.mypy_cache',
  '**/tests',
  '**/node_modules',
  '**/dist',
  '**/build',
  '**/*.pyc',
];

let dockerAvailableCache: boolean | undefined;

export function dockerAvailable(): boolean {
  if (dockerAvailableCache === undefined) {
    const cli = process.env.CDK_DOCKER ?? 'docker';
    const res = spawnSync(cli, ['version', '--format', '{{.Server.Version}}'], { stdio: 'ignore', timeout: 15_000 });
    dockerAvailableCache = !res.error && res.status === 0;
  }
  return dockerAvailableCache;
}

/**
 * Copies the importable Python packages of one service directory into `outDir`.
 * Supports both the `src/` layout (`services/worker/src/worker/...`) and the flat layout
 * (`services/worker/worker/...`). Dependencies are NOT installed - the result is only good for
 * `cdk synth` / unit tests, never for a deployment.
 */
function copyPackages(serviceDir: string, outDir: string): void {
  if (!fs.existsSync(serviceDir)) return;
  const root = fs.existsSync(path.join(serviceDir, 'src')) ? path.join(serviceDir, 'src') : serviceDir;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory() || IGNORED.has(entry.name) || entry.name.startsWith('.')) continue;
    const pkg = path.join(root, entry.name);
    if (!fs.existsSync(path.join(pkg, '__init__.py'))) continue;
    fs.cpSync(pkg, path.join(outDir, entry.name), {
      recursive: true,
      filter: (src) => !IGNORED.has(path.basename(src)) && !src.endsWith('.pyc'),
    });
  }
}

class SourceCopyBundling implements ILocalBundling {
  constructor(
    private readonly servicesDir: string,
    private readonly component: ServiceComponent,
    private readonly force: boolean,
  ) {}

  public tryBundle(outputDir: string, _options: BundlingOptions): boolean {
    // With Docker present and no explicit skip, let the real Docker bundling run.
    if (!this.force && dockerAvailable()) return false;
    copyPackages(path.join(this.servicesDir, 'shared'), outputDir);
    copyPackages(path.join(this.servicesDir, this.component), outputDir);
    fs.writeFileSync(
      path.join(outputDir, 'SYNTH_ONLY.txt'),
      'This asset was produced by the source-copy fallback: no third-party dependencies are installed.\n' +
        'It exists so `cdk synth` and unit tests run without Docker. Do NOT deploy it - build a real\n' +
        'artifact with infra/scripts/build-lambda-artifact.sh or synth with Docker available.\n',
    );
    return true;
  }
}

const LAMBDA_PLATFORM = ['--python-platform', 'aarch64-manylinux_2_28', '--python-version', '3.12'];

/** uv workspace package name of each component (see each services pyproject.toml). */
const PACKAGE: Record<ServiceComponent, string> = { worker: 'export-worker', api: 'export-api' };

/**
 * Docker bundling command, run in the AWS SAM Python 3.12 build image with the repository root mounted
 * at /asset-input. Third-party dependencies come from the uv lock file (exact pins, binary wheels for
 * linux/arm64, so an x86 host needs no QEMU); the two workspace packages are installed without deps.
 * Keep in sync with infra/scripts/build-lambda-artifact.sh.
 */
function dockerCommand(component: ServiceComponent): string[] {
  const platform = LAMBDA_PLATFORM.join(' ');
  const script = [
    'set -euo pipefail',
    'pip install --no-cache-dir --quiet "uv>=0.11,<0.12"',
    'cd /asset-input',
    `uv export --frozen --no-dev --no-emit-workspace --no-hashes --package ${PACKAGE[component]} -o /tmp/requirements.txt`,
    `uv pip install --no-cache --target /asset-output ${platform} --only-binary :all: -r /tmp/requirements.txt`,
    `uv pip install --no-cache --no-deps --target /asset-output ${platform} services/shared services/${component}`,
    "find /asset-output -name '__pycache__' -prune -exec rm -rf {} +",
  ].join(' && ');
  return ['bash', '-c', script];
}

/**
 * Asset source = repository root, narrowed (gitignore semantics) to the lock file and the two
 * workspace packages the function needs, so unrelated changes do not change the asset hash.
 */
function assetExcludes(component: ServiceComponent): string[] {
  return [
    '/*',
    '!/pyproject.toml',
    '!/uv.lock',
    '!/services',
    '/services/*',
    '!/services/shared',
    `!/services/${component}`,
    ...ASSET_EXCLUDES,
  ];
}

export function resolvePythonCode(opts: PythonCodeOptions): ResolvedCode {
  if (opts.artifact) {
    if (!fs.existsSync(opts.artifact)) {
      throw new Error(`${opts.component} artifact not found: ${opts.artifact}`);
    }
    return { code: lambda.Code.fromAsset(opts.artifact), source: 'artifact' };
  }

  // The services tree is built by a sibling package; tolerate its absence so infra tests stay hermetic.
  let assetDir = path.dirname(opts.servicesDir);
  if (!fs.existsSync(opts.servicesDir)) {
    assetDir = fs.mkdtempSync(path.join(os.tmpdir(), `export-pipeline-${opts.component}-`));
    fs.writeFileSync(path.join(assetDir, 'README.txt'), 'services/ directory not found at synth time\n');
  }

  const local = new SourceCopyBundling(opts.servicesDir, opts.component, opts.skipBundling);
  const code = lambda.Code.fromAsset(assetDir, {
    ignoreMode: IgnoreMode.GIT,
    exclude: assetExcludes(opts.component),
    bundling: {
      image: DockerImage.fromRegistry(lambda.Runtime.PYTHON_3_12.bundlingImage.image),
      command: dockerCommand(opts.component),
      environment: { HOME: '/tmp', UV_CACHE_DIR: '/tmp/uv-cache', PIP_DISABLE_PIP_VERSION_CHECK: '1' },
      local,
    },
  });
  const source: CodeSource = opts.skipBundling || !dockerAvailable() ? 'source-copy' : 'docker';
  return { code, source };
}
