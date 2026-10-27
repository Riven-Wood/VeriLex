"""Describe actual returned ranges without prescribing a reading workflow."""
def gaps(receipt):
    covered=0;end=0
    for lo,hi in sorted(receipt.get('ranges',[])):
        covered+=max(0,hi-max(end,lo));end=max(end,hi)
    result=list(receipt['gaps'])
    total=len(receipt['text'])
    if covered<total:result.append(f'本工具已返回 {covered}/{total} 字符；其余内容未由本工具读取。')
    return result
