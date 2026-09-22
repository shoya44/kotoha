"""テストの所要時間を、モジュールごと・1件ごとに測る。**測ってから決めるため。**

手元と CI（windows-latest）では速さが何倍も違う。どこに時間がかかっているかは
走らせた場所でしか分からないので、同じ数え方で両方で測れるようにしておく。

    python -m tools.time_tests            # 全部
    python -m tools.time_tests 15         # 遅いほうから15件だけ見せる
"""

import collections
import io
import sys
import time
import unittest


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.times = []

    def startTest(self, test):
        self._began = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        self.times.append((time.perf_counter() - self._began, test.id()))
        super().stopTest(test)


def main(top: int = 25) -> int:
    suite = unittest.TestLoader().discover("tests", top_level_dir=".")
    runner = unittest.TextTestRunner(stream=io.StringIO(), resultclass=TimedResult, verbosity=0)
    began = time.perf_counter()
    result = runner.run(suite)
    whole = time.perf_counter() - began
    print(f"{result.testsRun}件 {whole:.1f}秒  失敗={len(result.failures)} "
          f"エラー={len(result.errors)} 飛ばした={len(result.skipped)}")
    by_module = collections.defaultdict(lambda: [0, 0.0])
    for seconds, test_id in result.times:
        name = test_id.split(".")[1]
        by_module[name][0] += 1
        by_module[name][1] += seconds
    print(f"\n{'モジュール':22} {'件':>4} {'秒':>7}")
    for name, (count, seconds) in sorted(by_module.items(), key=lambda kv: -kv[1][1]):
        print(f"{name:22} {count:4} {seconds:7.2f}")
    print(f"\n遅いほうから{top}件")
    for seconds, test_id in sorted(result.times, reverse=True)[:top]:
        print(f"{seconds:6.2f}  {test_id.removeprefix('tests.')}")
    for test, trace in result.errors + result.failures:
        print(f"\n---- {test.id()}\n{trace.strip()}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 25))
