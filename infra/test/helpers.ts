import { App, Aspects } from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { AwsSolutionsChecks } from 'cdk-nag';
import { loadConfig } from '../lib/config';
import { ExportPipelineStack } from '../lib/export-pipeline-stack';

export interface Synth {
  readonly stack: ExportPipelineStack;
  readonly template: Template;
}

const cache = new Map<string, Synth>();

/** Synthesizes the stack for a context (memoized; bundling is always skipped in unit tests). */
export function synth(context: Record<string, string> = {}, opts: { nag?: boolean } = {}): Synth {
  const key = JSON.stringify({ context, opts });
  const hit = cache.get(key);
  if (hit) return hit;
  const app = new App({ context: { skipBundling: 'true', ...context } });
  const stack = new ExportPipelineStack(app, `ExportPipeline-${context.stage ?? 'dev'}`, {
    config: loadConfig(app.node),
  });
  if (opts.nag) Aspects.of(app).add(new AwsSolutionsChecks());
  const result = { stack, template: Template.fromStack(stack) };
  cache.set(key, result);
  return result;
}

type Statement = { Effect: string; Action: string | string[]; Resource: unknown; Condition?: unknown };

/** All IAM statements attached to a role whose logical id starts with `rolePrefix`. */
export function roleStatements(template: Template, rolePrefix: string): Statement[] {
  const policies = template.findResources('AWS::IAM::Policy');
  const out: Statement[] = [];
  for (const policy of Object.values(policies)) {
    const roles = (policy.Properties.Roles ?? []) as Array<{ Ref?: string }>;
    if (!roles.some((r) => r.Ref?.startsWith(rolePrefix))) continue;
    out.push(...(policy.Properties.PolicyDocument.Statement as Statement[]));
  }
  return out;
}

export function actionsOf(s: Statement): string[] {
  return Array.isArray(s.Action) ? s.Action : [s.Action];
}

export function resourcesOf(s: Statement): unknown[] {
  return Array.isArray(s.Resource) ? s.Resource : [s.Resource];
}
