import { Duration } from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import type * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions';
import type * as sqs from 'aws-cdk-lib/aws-sqs';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';

export interface MonitoringProps {
  readonly stage: string;
  readonly queue: sqs.IQueue;
  readonly dlq: sqs.IQueue;
  readonly worker: lambda.IFunction;
  readonly workerTimeout: Duration;
  readonly api: lambda.IFunction;
  readonly alarmEmail?: string;
  /** Oldest-message age that counts as a stuck pipeline. */
  readonly maxMessageAge?: Duration;
}

/**
 * Operational alarms for the export pipeline, all routed to one SNS topic.
 */
export class Monitoring extends Construct {
  public readonly topic: sns.Topic;
  public readonly alarms: cloudwatch.Alarm[] = [];

  constructor(scope: Construct, id: string, props: MonitoringProps) {
    super(scope, id);

    this.topic = new sns.Topic(this, 'AlarmTopic', {
      displayName: `Export pipeline alarms (${props.stage})`,
      enforceSSL: true,
    });
    NagSuppressions.addResourceSuppressions(this.topic, [
      {
        id: 'AwsSolutions-SNS2',
        reason:
          'Alarm notifications carry only alarm metadata (no export data). CloudWatch cannot publish to a topic ' +
          'encrypted with the AWS-managed SNS key; a customer-managed key adds cost without protecting anything sensitive.',
      },
    ]);
    if (props.alarmEmail) {
      this.topic.addSubscription(new subs.EmailSubscription(props.alarmEmail));
    }

    const action = new actions.SnsAction(this.topic);
    const add = (alarmId: string, alarmProps: cloudwatch.AlarmProps): void => {
      const alarm = new cloudwatch.Alarm(this, alarmId, alarmProps);
      alarm.addAlarmAction(action);
      alarm.addOkAction(action);
      this.alarms.push(alarm);
    };

    const prefix = `export-pipeline-${props.stage}`;

    add('DlqNotEmpty', {
      alarmName: `${prefix}-dlq-not-empty`,
      alarmDescription: 'An export message exhausted its retries. The job is marked failed / WORKER_ERROR; inspect and redrive.',
      metric: props.dlq.metricApproximateNumberOfMessagesVisible({ period: Duration.minutes(1), statistic: 'Maximum' }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    add('WorkerErrors', {
      alarmName: `${prefix}-worker-errors`,
      alarmDescription: 'The export worker Lambda raised unhandled errors.',
      metric: props.worker.metricErrors({ period: Duration.minutes(5), statistic: 'Sum' }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    add('WorkerDurationNearTimeout', {
      alarmName: `${prefix}-worker-duration-near-timeout`,
      alarmDescription: 'A worker invocation used more than 80% of its timeout; lower PART_ROWS before parts start timing out.',
      metric: props.worker.metricDuration({ period: Duration.minutes(5), statistic: 'Maximum' }),
      threshold: Math.floor(props.workerTimeout.toMilliseconds() * 0.8),
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    add('OldestMessageAge', {
      alarmName: `${prefix}-oldest-message-age`,
      alarmDescription: 'Export requests are waiting too long in the queue (worker throttled, failing, or disabled).',
      metric: props.queue.metricApproximateAgeOfOldestMessage({ period: Duration.minutes(5), statistic: 'Maximum' }),
      threshold: (props.maxMessageAge ?? Duration.minutes(30)).toSeconds(),
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    add('ApiErrors', {
      alarmName: `${prefix}-api-errors`,
      alarmDescription: 'The API Lambda raised unhandled errors.',
      metric: props.api.metricErrors({ period: Duration.minutes(5), statistic: 'Sum' }),
      threshold: 5,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
  }
}
