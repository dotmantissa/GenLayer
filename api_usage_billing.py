# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"


@allow_storage
@dataclass
class ApiPlan:
    operator: Address
    cost_per_call_wei: u256
    daily_limit_calls: u256


@allow_storage
@dataclass
class Subscription:
    active: bool
    max_daily_spend_wei: u256


@allow_storage
@dataclass
class DailyUsage:
    day_id: u256
    calls: u256
    spend_wei: u256


class ApiUsageBilling(gl.Contract):
    apis: TreeMap[str, ApiPlan]
    subscriptions: TreeMap[str, Subscription]          # key: consumer|api
    usage: TreeMap[str, DailyUsage]                    # key: consumer|api
    credit_balance: TreeMap[str, u256]                 # key: consumer
    total_deposited: TreeMap[str, u256]                # key: consumer
    warned_low_credit: TreeMap[str, bool]              # key: consumer
    subscribed_apis: TreeMap[str, str]                 # key: consumer -> json[list[str]]

    def __init__(self):
        pass

    def _api_key(self, api_name: str) -> str:
        return str(api_name).strip().lower()

    def _sub_key(self, consumer: str, api_name: str) -> str:
        return f"{str(consumer).lower()}|{self._api_key(api_name)}"

    def _day_id(self) -> int:
        return int(gl.block.timestamp) // 86_400

    def _usage_for_today(self, consumer: str, api_name: str) -> DailyUsage:
        key = self._sub_key(consumer, api_name)
        day = self._day_id()
        if key not in self.usage:
            return DailyUsage(day_id=u256(day), calls=u256(0), spend_wei=u256(0))
        u = self.usage[key]
        if int(u.day_id) != day:
            return DailyUsage(day_id=u256(day), calls=u256(0), spend_wei=u256(0))
        return u

    def _ensure_subscribed(self, consumer: str, api_name: str) -> Subscription:
        key = self._sub_key(consumer, api_name)
        if key not in self.subscriptions or not bool(self.subscriptions[key].active):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} consumer not subscribed to api")
        return self.subscriptions[key]

    def _get_consumer_credit(self, consumer: str) -> int:
        c = str(consumer).lower()
        return int(self.credit_balance[c]) if c in self.credit_balance else 0

    @gl.public.write
    def register_api(self, api_name: str, cost_per_call_wei: int, daily_limit_calls: int) -> None:
        api = self._api_key(api_name)
        if not api:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} api_name is required")
        if cost_per_call_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} cost_per_call_wei must be positive")
        if daily_limit_calls <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} daily_limit_calls must be positive")

        self.apis[api] = ApiPlan(
            operator=gl.message.sender_account,
            cost_per_call_wei=u256(cost_per_call_wei),
            daily_limit_calls=u256(daily_limit_calls),
        )
        print(
            f"[ApiRegistered] api={api} operator={gl.message.sender_account} "
            f"cost_per_call_wei={cost_per_call_wei} daily_limit_calls={daily_limit_calls}"
        )

    @gl.public.write
    def subscribe(self, api_name: str, max_daily_spend_wei: int, initial_deposit_wei: int) -> None:
        api = self._api_key(api_name)
        if api not in self.apis:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} api not registered")
        if max_daily_spend_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} max_daily_spend_wei must be positive")
        if initial_deposit_wei < 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} initial_deposit_wei cannot be negative")

        consumer = str(gl.message.sender_account).lower()
        sub_key = self._sub_key(consumer, api)
        self.subscriptions[sub_key] = Subscription(
            active=True,
            max_daily_spend_wei=u256(max_daily_spend_wei),
        )

        if initial_deposit_wei > 0:
            prev_credit = self._get_consumer_credit(consumer)
            self.credit_balance[consumer] = u256(prev_credit + int(initial_deposit_wei))
            prev_deposit = int(self.total_deposited[consumer]) if consumer in self.total_deposited else 0
            self.total_deposited[consumer] = u256(prev_deposit + int(initial_deposit_wei))

        if consumer not in self.subscribed_apis:
            self.subscribed_apis[consumer] = "[]"
        current = json.loads(self.subscribed_apis[consumer])
        if api not in current:
            current.append(api)
            self.subscribed_apis[consumer] = json.dumps(current)

        print(
            f"[Subscribed] consumer={consumer} api={api} max_daily_spend_wei={max_daily_spend_wei} "
            f"initial_deposit_wei={initial_deposit_wei}"
        )

    @gl.public.write
    def top_up_credit(self, amount: int) -> None:
        if amount <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} amount must be positive")
        consumer = str(gl.message.sender_account).lower()
        prev_credit = self._get_consumer_credit(consumer)
        self.credit_balance[consumer] = u256(prev_credit + int(amount))
        prev_deposit = int(self.total_deposited[consumer]) if consumer in self.total_deposited else 0
        self.total_deposited[consumer] = u256(prev_deposit + int(amount))
        self.warned_low_credit[consumer] = False
        print(f"[CreditToppedUp] consumer={consumer} amount={amount}")

    @gl.public.write
    def consume_credit(self, api_name: str, consumer_address: str) -> None:
        api = self._api_key(api_name)
        if api not in self.apis:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} api not registered")

        consumer = str(consumer_address).lower()
        plan = self.apis[api]
        sub = self._ensure_subscribed(consumer, api)

        usage = self._usage_for_today(consumer, api)
        next_calls = int(usage.calls) + 1
        next_spend = int(usage.spend_wei) + int(plan.cost_per_call_wei)

        if next_calls > int(plan.daily_limit_calls):
            print(
                f"[LimitExceeded] api={api} consumer={consumer} reason=daily_call_limit "
                f"attempted_calls={next_calls} max_calls={int(plan.daily_limit_calls)}"
            )
            raise gl.vm.UserError(f"{ERROR_EXPECTED} daily call limit exceeded")

        if next_spend > int(sub.max_daily_spend_wei):
            print(
                f"[LimitExceeded] api={api} consumer={consumer} reason=daily_spend_limit "
                f"attempted_spend={next_spend} max_spend={int(sub.max_daily_spend_wei)}"
            )
            raise gl.vm.UserError(f"{ERROR_EXPECTED} max daily spend exceeded")

        credit = self._get_consumer_credit(consumer)
        if credit < int(plan.cost_per_call_wei):
            print(
                f"[LimitExceeded] api={api} consumer={consumer} reason=insufficient_credit "
                f"credit={credit} required={int(plan.cost_per_call_wei)}"
            )
            raise gl.vm.UserError(f"{ERROR_EXPECTED} insufficient prepaid credit")

        remaining = credit - int(plan.cost_per_call_wei)
        self.credit_balance[consumer] = u256(remaining)
        self.usage[self._sub_key(consumer, api)] = DailyUsage(
            day_id=u256(self._day_id()),
            calls=u256(next_calls),
            spend_wei=u256(next_spend),
        )

        print(
            f"[CallBilled] api={api} consumer={consumer} billed_wei={int(plan.cost_per_call_wei)} "
            f"remaining_credit_wei={remaining} calls_today={next_calls} spend_today_wei={next_spend}"
        )

        total = int(self.total_deposited[consumer]) if consumer in self.total_deposited else 0
        warned = bool(self.warned_low_credit[consumer]) if consumer in self.warned_low_credit else False
        if total > 0 and not warned and (remaining * 100) <= (20 * total):
            self.warned_low_credit[consumer] = True
            print(
                f"[CreditDepleted] consumer={consumer} remaining_credit_wei={remaining} "
                f"threshold=20_percent_of_total_deposit"
            )

    @gl.public.view
    def get_usage_report(self, consumer_address: str) -> str:
        consumer = str(consumer_address).lower()
        apis = json.loads(self.subscribed_apis[consumer]) if consumer in self.subscribed_apis else []
        report = {
            "consumer": consumer,
            "day_id": self._day_id(),
            "credit_balance_wei": self._get_consumer_credit(consumer),
            "apis": [],
        }

        for api in apis:
            plan = self.apis[api] if api in self.apis else None
            sub_key = self._sub_key(consumer, api)
            usage = self._usage_for_today(consumer, api)
            sub = self.subscriptions[sub_key] if sub_key in self.subscriptions else None

            report["apis"].append(
                {
                    "api_name": api,
                    "calls_today": int(usage.calls),
                    "spend_today_wei": int(usage.spend_wei),
                    "cost_per_call_wei": int(plan.cost_per_call_wei) if plan else 0,
                    "api_daily_limit_calls": int(plan.daily_limit_calls) if plan else 0,
                    "consumer_max_daily_spend_wei": int(sub.max_daily_spend_wei) if sub else 0,
                }
            )

        return json.dumps(report)


    @gl.public.view
    def consumer_integration_example(self) -> str:
        return "Use this pattern in consumer contract: billing.consume_credit(api_name, str(gl.message.sender_account)) before gl.nondet.web.get(url)."
