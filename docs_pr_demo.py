"""PR 功能演示用的一次性模块——测完即可删，非项目代码。"""


# 计算列表均值（故意留一个可被评审指出的边界问题：空列表除零）
def mean(nums):
    total = 0
    for n in nums:
        total += n
    return total / len(nums)


# 把名字首字母大写
def capitalize_name(name):
    return name[0].upper() + name[1:]
