import csv
import io
import json
from zipfile import ZipFile, ZIP_DEFLATED

INSTRUCTIONS = """# 决策复核计时
请按 cases.json 的顺序逐项判断，将结果填入 timing.csv。任务、范围和原始证据可在 tasks.json 和 dataset.json 查询。每条从开始阅读到作出决定记录实际起止时间，使用含时区的 ISO 时间，如 2026-11-21T14:25:10+08:00。暂停时另行注明，选择连续用时完成该条。记录姓名及是否接触过相同内容（previously_seen 填 yes、no 或 unsure）。
方向按窗口 USDC 名义占比末减初的符号填 increasing、decreasing 或 unchanged。峰值时点填允许样本中失衡最大点的 UTC 小时（YYYY-MM-DDTHH:00:00Z）。集中度告警在最大合规相邻一小时增幅达到 1 个百分点时填 escalate，否则填 no_alert。输入报告缺失时用原始证据自行核算。
程序可接收部分已完成的个案，其余整行的填写栏留空。由一名审阅人提交每个编号的一次实际复核。请只返回 timing.csv，勿查阅项目结果或其他人的答案。计时与此前的 143 项正文标注是两项不同的测量。
"""


def make_packet(output, cases, baseline):
    with ZipFile(output/'决策复核计时包.zip','x',compression=ZIP_DEFLATED) as archive:
        archive.writestr('README.md',INSTRUCTIONS)
        stream=io.StringIO(newline='')
        writer=csv.writer(stream)
        writer.writerow(('case_id','reviewer','start_iso','end_iso','decision','previously_seen'))
        for row in cases:
            writer.writerow((row['case_id'],'','','','',''))
        archive.writestr('timing.csv',stream.getvalue().encode('utf-8-sig'))
        archive.writestr('cases.json',json.dumps(cases,ensure_ascii=False,indent=2))
        for name in ('tasks.json','dataset.json'):
            archive.write(baseline/'results/current_preparation'/name,name)
