# Deploying with AWS SAM

The whole system is one CloudFormation stack described by
[`infrastructure/template.yaml`](../infrastructure/template.yaml) — eighteen
resources, one command.

---

## Prerequisites

- **AWS CLI**, configured with credentials that can create IAM roles
- **AWS SAM CLI** —
  [installation guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- **Python 3.12**, matching the Lambda runtime, so `sam build` resolves
  dependencies against the right version

```bash
sam --version
aws sts get-caller-identity
```

## Deploy

```bash
cd infrastructure
sam build
sam deploy --guided
```

`sam build` copies each function's source into `.aws-sam/build/` and installs
anything listed in that function's `requirements.txt`. `--guided` then prompts
for a stack name, region, the parameters below, and whether to allow IAM role
creation — answer **yes** to that one, since the template creates a scoped
execution role per function.

Your answers are written to `samconfig.toml`, so every deploy after the first is:

```bash
sam build && sam deploy
```

`samconfig.toml` is gitignored because it pins an account-specific deployment
bucket. [`samconfig.toml.example`](../infrastructure/samconfig.toml.example)
shows its shape.

## Parameters

All four are optional and default to empty.

| Parameter | Purpose |
|---|---|
| `ApiDomainName` | Custom domain, e.g. `api.redsparrowenterprise.in`. Blank uses the generated `execute-api` URL |
| `AcmCertificateArn` | Certificate covering that domain. **Must be issued in the same region as the stack** |
| `HostedZoneId` | Route 53 hosted zone. Supply it to have the stack create the DNS alias record too |
| `OpsAlertEmail` | Where CloudWatch alarms go. Deliberately separate from the customer notification topic |

Non-interactively:

```bash
sam deploy \
  --parameter-overrides \
    ApiDomainName=api.redsparrowenterprise.in \
    AcmCertificateArn=arn:aws:acm:ap-south-1:ACCOUNT_ID:certificate/xxxxxxxx \
    HostedZoneId=Z0123456789ABCDEF \
    OpsAlertEmail=ops@example.com
```

Custom domain detail, including the two prerequisites that catch people out, is
in **[custom-domain.md](custom-domain.md)**.

## What the template creates

| Resource | Name | Notes |
|---|---|---|
| DynamoDB table | `Orders` | On-demand, encrypted, PITR on, TTL on `ttl`, two GSIs |
| SQS queue | `OrderQueue` | 30s visibility timeout, redrive to DLQ after 3 receives |
| SQS queue | `OrderQueueDLQ` | 14-day retention |
| SQS queue | `NotificationDLQ` | Async invocation failures from `NotificationService` |
| SNS topic | `OrderNotification` | Customer notifications |
| SNS topic | `OpsAlerts` | Alarm destination, with optional email subscription |
| Lambda | `OrderService` | `POST /order` integration |
| Lambda | `PaymentService` | SQS trigger, batch size 10, partial batch failures on |
| Lambda | `NotificationService` | SNS trigger, DLQ configured |
| HTTP API | `prod` stage | JSON access logs, 30-day retention |
| CloudWatch alarms | 3 | `OrderService` errors, `PaymentService` errors, DLQ depth |
| IAM roles | 3 | One per function, generated from SAM policy templates |

### Three details worth reading in the template

**The IAM is generated, not written.** Each function declares what it needs by
reference:

```yaml
Policies:
  - DynamoDBWritePolicy:
      TableName: !Ref OrdersTable
  - SQSSendMessagePolicy:
      QueueName: !GetAtt OrderQueue.QueueName
```

SAM expands those into scoped policies with the correct ARNs. Compare that to
[the hand-written equivalents on `main`](../../tree/main/docs/console-walkthrough.md#step-4--iam-execution-roles),
where a mistyped region or account ID produces an `AccessDeniedException` at
runtime rather than an error at deploy time.

**Partial batch failures are declared in two places, and both must agree.**

```yaml
FunctionResponseTypes:
  - ReportBatchItemFailures
```

The handler returns a `batchItemFailures` list; without this on the event source
mapping AWS ignores that response and retries all ten messages whenever one
fails.

**The alarms have an action.** Each `AWS::CloudWatch::Alarm` carries
`AlarmActions: [!Ref OpsAlertsTopic]`. An alarm with no action is a dashboard
widget, not monitoring.

## After deploying

**1. Subscribe an email to `OrderNotification`.** The template creates the topic
but deliberately does not subscribe anyone — the address is personal, not
infrastructural, and hardcoding it would put it in version control.

```bash
aws sns subscribe \
  --topic-arn "$(aws cloudformation describe-stacks \
    --stack-name serverless-order-processing \
    --query "Stacks[0].Outputs[?OutputKey=='NotificationTopicArn'].OutputValue" \
    --output text)" \
  --protocol email \
  --notification-endpoint you@example.com
```

Then click the confirmation link. Until you do, the subscription sits at
`PendingConfirmation` and delivers nothing.

**2. Confirm the `OpsAlerts` subscription** as well, if you passed
`OpsAlertEmail`.

**3. Test it.**

```bash
sam list stack-outputs --stack-name serverless-order-processing
```

```bash
curl -X POST https://YOUR_API_ID.execute-api.ap-south-1.amazonaws.com/prod/order \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: test-001" \
  -d '{
        "customerId": "cust_123",
        "items": [{"sku": "PIZZA_MARGHERITA", "quantity": 2}],
        "amount": 24.99
      }'
```

```json
{"orderId": "test-001", "status": "PLACED"}
```

Within a few seconds, and with no further client involvement, the confirmation
arrives from the `OrderNotification` subscription you confirmed in step 1:

![Email from AWS Notifications with subject Order domain-test-002 PAID](../screenshots/04-order-confirmation-email.png)

That email is the end-to-end proof: the queue was consumed, payment settled, the
order transitioned from `PLACED` to `PAID`, and SNS fanned the result out.

**[→ Full verification with screenshots](outputs.md)**

## Iterating

```bash
sam build && sam deploy          # push a code or template change
sam logs -n PaymentService --stack-name serverless-order-processing --tail
sam local invoke OrderServiceFunction -e events/order.json   # requires Docker
```

`sam deploy` presents a changeset before applying it, so you see exactly which
resources will be added, modified or replaced before confirming. Watch for
**Replacement: True** on the DynamoDB table — that means data loss.

## A second environment

```bash
sam deploy --stack-name serverless-order-processing-staging
```

One caveat: the template pins physical names (`Orders`, `OrderQueue`,
`OrderNotification`), so a second stack in the *same account and region* will
collide on those names. Deploy the second environment to a different region or
account, or make the names a parameter — `TableName: !Sub "Orders-${Env}"` —
before running environments side by side.

## Teardown

```bash
sam delete --stack-name serverless-order-processing
```

Every resource in the table above, in dependency order, automatically. Two
things survive by design: the S3 deployment bucket the SAM CLI created, and the
CloudWatch log groups Lambda created implicitly. The API access log group *is*
in the stack and does get removed.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Requires capabilities : [CAPABILITY_IAM]` | Pass `--capabilities CAPABILITY_IAM`, or answer yes to the IAM prompt under `--guided` |
| `Orders already exists` | Something already owns that name — most likely a stack from the console walkthrough. Delete it, or rename in the template |
| `Unable to import module 'app'` | `CodeUri` no longer matches the source layout, or `sam build` was skipped and `sam deploy` packaged the raw tree |
| Stack fails on `ApiCustomDomain` | The ACM certificate is in a different region from the stack |
| Stack rolls back on `ApiDnsRecord` | `HostedZoneId` belongs to a zone that is not authoritative for the domain |
| Deploy succeeds, no email | Nobody is subscribed to `OrderNotification`, or the subscription is unconfirmed |
| Orders stay `PLACED` | `PaymentService` is erroring. `sam logs -n PaymentService --tail` will show it |
