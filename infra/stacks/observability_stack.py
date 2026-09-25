from aws_cdk import Stack
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct


class ObservabilityStack(Stack):
    """CloudWatch dashboard/alarms + AWS Budgets cost guardrails - see
    docs/designs/05-observability-cost.md, "Cost budget" and "Metrics to
    monitor". Deliberately references other stacks' resources by name/ARN
    (not by importing their L2 constructs) so this stack has no hard
    ordering dependency on them - a dashboard widget for a metric with no
    data yet (e.g. before another stack finishes deploying) fails
    silently, not with a CFN error, which is the right failure mode for a
    monitoring stack.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        alert_email: str,
        dynamodb_table_names: list[str],
        reviews_table_name: str,
        opensearch_domain_name: str,
        state_machine_arn: str,
        api_lambda_functions: list[lambda_.IFunction],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.alerts_topic = sns.Topic(self, "AlertsTopic", topic_name="rag-ecommerce-alerts")
        self.alerts_topic.add_subscription(subscriptions.EmailSubscription(alert_email))
        # AWS Budgets needs explicit permission to publish to a topic it
        # doesn't own - not granted automatically by add_subscription.
        self.alerts_topic.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["sns:Publish"],
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                resources=[self.alerts_topic.topic_arn],
            )
        )

        self._build_budget()
        self._build_dashboard(dynamodb_table_names, opensearch_domain_name, state_machine_arn, api_lambda_functions)
        self._build_alarms(reviews_table_name, state_machine_arn, api_lambda_functions)

    def _build_budget(self) -> None:
        def notification(threshold: float) -> budgets.CfnBudget.NotificationWithSubscribersProperty:
            return budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type="ACTUAL",
                    comparison_operator="GREATER_THAN",
                    threshold=threshold,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="SNS", address=self.alerts_topic.topic_arn)],
            )

        # $80 warning (80% of $100) and $100 alert (100%) - design doc 05's
        # "Guardrails". ACTUAL (not FORECASTED) so it fires on real spend,
        # not a projection that might not materialize.
        budgets.CfnBudget(
            self,
            "MonthlyCostBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=100, unit="USD"),
            ),
            notifications_with_subscribers=[notification(80), notification(100)],
        )

    def _build_dashboard(
        self,
        dynamodb_table_names: list[str],
        opensearch_domain_name: str,
        state_machine_arn: str,
        api_lambda_functions: list[lambda_.IFunction],
    ) -> None:
        dynamodb_capacity_widget = cloudwatch.GraphWidget(
            title="DynamoDB - Consumed Capacity",
            left=[
                cloudwatch.Metric(namespace="AWS/DynamoDB", metric_name=metric, dimensions_map={"TableName": table}, statistic="Sum")
                for table in dynamodb_table_names
                for metric in ("ConsumedReadCapacityUnits", "ConsumedWriteCapacityUnits")
            ],
        )
        dynamodb_throttle_widget = cloudwatch.GraphWidget(
            title="DynamoDB - Throttled Requests",
            left=[
                cloudwatch.Metric(namespace="AWS/DynamoDB", metric_name="ThrottledRequests", dimensions_map={"TableName": table}, statistic="Sum")
                for table in dynamodb_table_names
            ],
        )
        opensearch_widget = cloudwatch.GraphWidget(
            title="OpenSearch - Cluster Health & Latency",
            left=[
                cloudwatch.Metric(namespace="AWS/ES", metric_name=f"ClusterStatus.{color}", dimensions_map={"DomainName": opensearch_domain_name, "ClientId": self.account})
                for color in ("green", "yellow", "red")
            ],
            right=[cloudwatch.Metric(namespace="AWS/ES", metric_name="SearchLatency", dimensions_map={"DomainName": opensearch_domain_name, "ClientId": self.account}, statistic="Average")],
        )
        bedrock_widget = cloudwatch.GraphWidget(
            title="Bedrock - Invocations & Throttles",
            left=[
                cloudwatch.Metric(namespace="AWS/Bedrock", metric_name="Invocations", statistic="Sum"),
                cloudwatch.Metric(namespace="AWS/Bedrock", metric_name="InvocationThrottles", statistic="Sum"),
                cloudwatch.Metric(namespace="AWS/Bedrock", metric_name="InvocationClientErrors", statistic="Sum"),
            ],
            right=[cloudwatch.Metric(namespace="AWS/Bedrock", metric_name="InvocationLatency", statistic="Average")],
        )
        step_functions_widget = cloudwatch.GraphWidget(
            title="Step Functions - Refresh Pipeline Executions",
            left=[
                cloudwatch.Metric(namespace="AWS/States", metric_name="ExecutionsSucceeded", dimensions_map={"StateMachineArn": state_machine_arn}, statistic="Sum"),
                cloudwatch.Metric(namespace="AWS/States", metric_name="ExecutionsFailed", dimensions_map={"StateMachineArn": state_machine_arn}, statistic="Sum"),
            ],
        )
        lambda_widget = cloudwatch.GraphWidget(
            title="API Lambdas - Errors & Duration",
            left=[cloudwatch.Metric(namespace="AWS/Lambda", metric_name="Errors", dimensions_map={"FunctionName": fn.function_name}, statistic="Sum") for fn in api_lambda_functions],
            right=[cloudwatch.Metric(namespace="AWS/Lambda", metric_name="Duration", dimensions_map={"FunctionName": fn.function_name}, statistic="Average") for fn in api_lambda_functions],
        )
        eval_widget = cloudwatch.GraphWidget(
            title="Eval - Retrieval & Groundedness (RAGEcommerce/Eval)",
            left=[
                cloudwatch.Metric(namespace="RAGEcommerce/Eval", metric_name="RetrievalPrecisionAtK", statistic="Average"),
                cloudwatch.Metric(namespace="RAGEcommerce/Eval", metric_name="CitationGroundingRate", statistic="Average"),
                cloudwatch.Metric(namespace="RAGEcommerce/Eval", metric_name="JudgeGroundednessScore", statistic="Average"),
            ],
            right=[cloudwatch.Metric(namespace="RAGEcommerce/Eval", metric_name="CostPerQueryUsd", statistic="Average")],
        )

        cloudwatch.Dashboard(
            self,
            "Dashboard",
            dashboard_name="rag-ecommerce",
            widgets=[
                [dynamodb_capacity_widget, dynamodb_throttle_widget],
                [opensearch_widget, bedrock_widget],
                [step_functions_widget, lambda_widget],
                [eval_widget],
            ],
        )

    def _build_alarms(self, reviews_table_name: str, state_machine_arn: str, api_lambda_functions: list[lambda_.IFunction]) -> None:
        alarm_action = cw_actions.SnsAction(self.alerts_topic)

        cloudwatch.Alarm(
            self,
            "RefreshPipelineFailedAlarm",
            alarm_name="rag-ecommerce-refresh-pipeline-failed",
            metric=cloudwatch.Metric(namespace="AWS/States", metric_name="ExecutionsFailed", dimensions_map={"StateMachineArn": state_machine_arn}, statistic="Sum"),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        ).add_alarm_action(alarm_action)

        for fn in api_lambda_functions:
            cloudwatch.Alarm(
                self,
                f"{fn.node.id}ErrorAlarm",
                alarm_name=f"rag-ecommerce-{fn.function_name}-errors",
                metric=cloudwatch.Metric(namespace="AWS/Lambda", metric_name="Errors", dimensions_map={"FunctionName": fn.function_name}, statistic="Sum"),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            ).add_alarm_action(alarm_action)

        # Reviews is the table under the most write load (delta refresh +
        # co-reviewed graph edges) - the one most likely to ever throttle.
        # Passed explicitly, not detected by substring-matching table names
        # in dynamodb_table_names - those are unresolved CDK tokens at
        # synth time (e.g. "${Token[TOKEN.123]}"), not literal strings, so
        # a "Reviews" in name check silently never matches and falls back
        # to the wrong table (caught by inspecting `cdk synth` output: the
        # alarm's Dimensions pointed at ProductsTable's export, not
        # Reviews').
        cloudwatch.Alarm(
            self,
            "ReviewsTableThrottleAlarm",
            alarm_name="rag-ecommerce-reviews-table-throttled",
            metric=cloudwatch.Metric(namespace="AWS/DynamoDB", metric_name="ThrottledRequests", dimensions_map={"TableName": reviews_table_name}, statistic="Sum"),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        ).add_alarm_action(alarm_action)
