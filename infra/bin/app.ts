#!/usr/bin/env node
import { App, Aspects, Tags } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { loadConfig } from '../lib/config';
import { ExportPipelineStack } from '../lib/export-pipeline-stack';

const app = new App();
const config = loadConfig(app.node);

// An explicit env is only required when importing an existing VPC (Vpc.fromLookup).
const env =
  process.env.CDK_DEFAULT_ACCOUNT && process.env.CDK_DEFAULT_REGION
    ? { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION }
    : undefined;

const stack = new ExportPipelineStack(app, `ExportPipeline-${config.stage}`, {
  config,
  env,
  description: `Async export pipeline (${config.stage}): HTTP API + SQS + Lambda worker + S3 + DynamoDB`,
});

Tags.of(stack).add('project', 'async-export-pipeline');
Tags.of(stack).add('stage', config.stage);

Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
