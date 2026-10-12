from zoo_framework.core import Master
from workers.sample_worker import SampleWorker

# Demonstration of import-time wiring and the ordering constraints:
#   conf   - registers the config hook; MUST come before Master(), the hook
#            runs while Master is constructed
#   params - resolves config.json; MUST come after the config is loaded
#   events - registers the event reactor
import conf.demo_conf  # noqa: F401
from workers.agent_worker import AgentWorker
# zfc:worker-imports

WORKERS = [
    # 开箱即跑的示例；确认业务后连同上方导入行一起删除
    ("SampleWorker", SampleWorker),
    ("AgentWorker", AgentWorker),
    # zfc:worker-registrations
]


def main():
    master = Master()

    # After Master(): the config is loaded and the event channels are ready.
    import events.demo_event  # noqa: F401
    import params.demo_params  # noqa: F401

    for name, worker_class in WORKERS:
        master.register_worker(name, worker_class)
    master.run()


if __name__ == '__main__':
    main()
