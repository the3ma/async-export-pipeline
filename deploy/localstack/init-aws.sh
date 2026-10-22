#!/usr/bin/env bash
# Runs inside LocalStack on startup (ready.d hook): creates the same resources the CDK stack creates in AWS.
set -euo pipefail
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

awslocal s3 mb s3://exports
awslocal s3api put-bucket-lifecycle-configuration --bucket exports --lifecycle-configuration '{
  "Rules":[
    {"ID":"expire-exports","Status":"Enabled","Filter":{"Prefix":"exports/"},"Expiration":{"Days":7}},
    {"ID":"abort-mpu","Status":"Enabled","Filter":{"Prefix":""},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":1}}
  ]}'

DLQ_URL=$(awslocal sqs create-queue --queue-name export-requests-dlq --query QueueUrl --output text)
DLQ_ARN=$(awslocal sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)
awslocal sqs create-queue --queue-name export-requests --attributes "{
  \"VisibilityTimeout\":\"960\",
  \"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"
}" >/dev/null

awslocal dynamodb create-table --table-name exports \
  --attribute-definitions AttributeName=export_id,AttributeType=S AttributeName=owner_id,AttributeType=S AttributeName=requested_at,AttributeType=S \
  --key-schema AttributeName=export_id,KeyType=HASH \
  --global-secondary-indexes '[{"IndexName":"gsi_owner_requested","KeySchema":[{"AttributeName":"owner_id","KeyType":"HASH"},{"AttributeName":"requested_at","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}]' \
  --billing-mode PAY_PER_REQUEST >/dev/null
awslocal dynamodb update-time-to-live --table-name exports --time-to-live-specification Enabled=true,AttributeName=expires_at >/dev/null

echo "localstack: exports bucket, export-requests (+dlq) queue and exports table ready in $REGION"
