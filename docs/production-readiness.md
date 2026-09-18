# Production Readiness

This project started as a "wire five AWS services together" exercise. The table
below is the gap list between that version and this one: what was missing, why
it matters once real traffic arrives, and what closed it.

| Gap | Why it hurts in production | How it is addressed |
|---|---|---|
| No input validation | Bad requests silently create garbage orders that surface later as support tickets | Payload validation in `OrderService`, returning `400` with per-field detail |
| No idempotency | Network retries and at-least-once SQS delivery create duplicate orders and double charges | `ConditionExpression` on the DynamoDB put plus an optional `Idempotency-Key` header; conditional status transition in `PaymentService` |
| No error handling in the functions | One bad message kills a whole batch and nobody finds out | Explicit exception handling, structured JSON logging, SQS partial batch failure reporting |
| No dead letter queues | Failed messages retry forever or vanish | `OrderQueueDLQ` and `NotificationDLQ` with `maxReceiveCount: 3` |
| No monitoring or alerting | You learn about the outage from customers | CloudWatch alarms on Lambda errors and DLQ depth, publishing to an ops topic; X-Ray tracing on every function |
| Status never updated after payment | Orders sit in `PLACED` forever even when payment succeeded | `PaymentService` transitions to `PAID` or `PAYMENT_FAILED` |
| Notifications hardcoded into payment | Adding SMS or Slack means editing payment logic | Separate `NotificationService` subscribed to SNS |
| Broad IAM permissions | One compromised function reaches everything | Per-function roles scoped to a single table, queue, or topic |
| No infrastructure as code | Console clicks are not reproducible, reviewable, or revertable | Full AWS SAM template in [`infrastructure/template.yaml`](../infrastructure/template.yaml) |
| No API authentication | Anyone on the internet can place orders | Documented Cognito / Lambda authorizer wiring; see [Roadmap](#roadmap) |
| No encryption or backup | Compliance exposure and permanent data loss | DynamoDB encryption at rest and point-in-time recovery |
| No secondary indexes | "All orders for this customer" needs a full table scan | `CustomerIndex` and `StatusIndex` GSIs |
| No CI | Broken code reaches deploy | GitHub Actions workflow running the test suite and `sam validate --lint` on every push and pull request |

## Least privilege, and what it costs you

Scoping each role to exactly what its function needs is the right call, and it
is also the part most likely to bite during a first deployment: a missing
action does not surface as a deployment error, it surfaces at runtime as an
`AccessDeniedException` inside the function, three retries later, in the dead
letter queue. [`outputs.md`](outputs.md) walks through exactly that happening
and how the alarm surfaced it — which is, in fairness, the system working as
designed.

The permissions each function actually needs:

| Function | Actions | Resource |
|---|---|---|
| `OrderService` | `dynamodb:PutItem` | `table/Orders` |
| | `sqs:SendMessage` | `OrderQueue` |
| `PaymentService` | `dynamodb:UpdateItem` | `table/Orders` |
| | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` | `OrderQueue` |
| | `sns:Publish` | `OrderNotification` |
| `NotificationService` | `dynamodb:UpdateItem` | `table/Orders` |
| | `sqs:SendMessage` | `NotificationDLQ` |

Every function additionally needs `AWSLambdaBasicExecutionRole` for CloudWatch
Logs, and `AWSXRayDaemonWriteAccess` if tracing is on.

## Roadmap

Honest about what is still missing:

- **API authentication.** Orders are tied to a `customerId` string the client
  supplies, which a client can spoof. Amazon Cognito with a JWT authorizer on
  the route is the fix; the SAM template has the hook commented in place.
- **Real payment integration.** `_process_payment` simulates a ~90% success
  rate so both code paths are visible in a demo. A real gateway call needs
  timeouts, retry classification (retryable failure vs. terminal decline), and
  credentials in AWS Secrets Manager.
- **Step Functions.** Once the flow grows branches — fraud checks, inventory
  reservation, refunds — an SQS chain becomes hard to reason about. A state
  machine makes the orchestration visible and auditable.
- **Request validation at the edge.** A JSON Schema model on the API Gateway
  route would reject malformed bodies before they reach Lambda at all.
- **A redrive path.** Messages in `OrderQueueDLQ` currently need manual
  replay. SQS supports redrive back to the source queue; wiring that into a
  runbook is the difference between an alarm and a recovery.
- **Load testing.** Artillery or Locust against the endpoint, to confirm
  DynamoDB on-demand scaling and Lambda concurrency behave under burst.
- **Multi-region.** DynamoDB global tables and Route 53 failover, if the
  recovery objective ever justifies the cost.
