# asyncio 与 create_task 学习笔记

## 一、核心概念

### 1.1 什么是 asyncio

`asyncio` 是 Python 的异步编程库，提供了单线程内的并发能力。

**核心组件：**
- **事件循环 (Event Loop)**：调度协程执行的核心引擎
- **协程 (Coroutine)**：可以挂起和恢复的异步函数
- **Task**：协程的包装器，用于事件循环调度
- **Future**：表示异步操作的结果

### 1.2 create_task 的作用

```python
task = asyncio.create_task(coro)
```

**作用**：将协程包装成 Task 对象，添加到事件循环的就绪队列，让它在后台异步执行。

**核心特点：**
- 立即返回，不等待协程执行完成
- 协程在后台异步运行
- 可通过 `await task` 等待结果
- 可通过 `task.cancel()` 取消任务

---

## 二、底层原理

### 2.1 伪代码实现

```python
class EventLoop:
    def __init__(self):
        self._ready_queue = []      # 就绪队列：存放可立即执行的 Task
        self._waiting_queue = {}   # 等待队列：存放等待特定事件的 Task
    
    def create_task(self, coro):
        """
        创建 Task 并加入就绪队列
        
        底层逻辑：
        1. 将协程包装成 Task 对象
        2. 将 Task 添加到就绪队列
        3. 立即返回 Task 对象
        """
        task = Task(coro, loop=self)
        self._ready_queue.append(task)
        return task
    
    def run_forever(self):
        """
        事件循环主循环
        
        工作流程：
        1. 检查等待队列，将到期任务移到就绪队列
        2. 从就绪队列取出 Task 执行一步
        3. 如果 Task await 了，挂起并注册回调
        4. 如果 Task 完成，移出队列
        """
        while True:
            # Step 1: 检查等待队列
            now = time.time()
            for wake_time, task in list(self._waiting_queue.items()):
                if wake_time <= now:
                    self._ready_queue.append(task)
                    del self._waiting_queue[wake_time]
            
            # Step 2: 执行就绪队列
            if self._ready_queue:
                task = self._ready_queue.pop(0)
                task._step()  # 执行协程的下一步
                
                # Step 3: 判断 Task 状态
                if task.state == "WAITING":
                    # 等待特定事件，注册回调
                    task._waiting_event.add_done_callback(
                        lambda: self._ready_queue.append(task)
                    )
                elif task.state == "RUNNING":
                    # 继续运行，放回就绪队列
                    self._ready_queue.append(task)
                # FINISHED：不处理，Task 已完成
            else:
                # 队列都为空，退出
                break


class Task:
    def __init__(self, coro, loop):
        self._coro = coro        # 协程对象
        self._loop = loop        # 事件循环引用
        self.state = "READY"     # 状态：READY | RUNNING | WAITING | FINISHED
        self._result = None      # 执行结果
    
    def _step(self):
        """
        执行协程的一步
        
        工作流程：
        1. 恢复/执行协程到下一个 await 点
        2. 如果协程 await 了，挂起 Task
        3. 如果协程完成，设置结果
        """
        self.state = "RUNNING"
        try:
            result = self._coro.send(None)
            
            if result is an awaitable:
                # 协程 await 了某个事件
                self.state = "WAITING"
                self._waiting_event = result
            else:
                # 协程继续运行
                self.state = "RUNNING"
                
        except StopIteration as e:
            # 协程执行完毕
            self.state = "FINISHED"
            self._result = e.value


class Queue:
    """异步队列"""
    def __init__(self):
        self._queue = []
        self._waiters = []  # 等待数据的协程
    
    async def get(self):
        """
        从队列获取数据，队列为空时挂起等待
        
        伪代码：
        1. 如果队列有数据，直接返回
        2. 如果队列为空，挂起当前 Task
        3. 等待有数据时恢复
        """
        if self._queue:
            return self._queue.pop(0)
        else:
            # 挂起当前 Task，等待数据
            current_task = asyncio.current_task()
            self._waiters.append(current_task)
            current_task.state = "WAITING"
            current_task._waiting_event = self._has_data_event
            # 事件循环会自动恢复这个 Task
    
    def put_nowait(self, item):
        """
        向队列放入数据（非阻塞）
        
        伪代码：
        1. 直接放入队列
        2. 通知等待的 Task
        """
        self._queue.append(item)
        # 通知等待的 Task 可以恢复了
        for task in self._waiters:
            self._waiters.remove(task)
            task.state = "READY"
            # 事件循环会将 task 移到就绪队列
```

---

## 三、执行流程图解

### 3.1 create_task 调用时序

```
时间线 →

调用者:  [create_task(coro)]  [继续执行...]  [await task]
                │                    │              │
                ▼                    │              │
事件循环:  [创建Task]  [加入就绪队列]  [返回Task]  [调度执行Task]  [Task完成]  [返回结果]
                │                    │              │              │          │
                ▼                    ▼              ▼              ▼          ▼
Task状态:  READY → 加入队列 → READY → RUNNING → FINISHED → 结果可用
```

### 3.2 多 Task 调度时序

```
时间线 →

Task A:  [Step1]  [await 1s]  [挂起]  [Step2]  [完成]
                    │                   │        │
                    ▼                   ▼        ▼
Task B:             [Step1]  [await 0.5s]  [挂起]  [Step2]  [完成]
                            │                   │        │
                            ▼                   ▼        ▼
Task C:                      [Step1]  [await 2s]  [挂起]  [Step2]  [完成]
```

### 3.3 队列生产者-消费者模型

```
生产者 (emit):  [put(r1)]  [put(r2)]  [put(r3)]
                    │         │         │
                    ▼         ▼         ▼
队列:          [r1]  [r2]  [r3]
                    │         │         │
                    ▼         ▼         ▼
消费者 (_drain):  [get]  [写入r1]  [get]  [写入r2]  [get]  [写入r3]
```

---

## 四、核心特性

### 4.1 单线程内的并发

```python
# 所有 Task 都在同一个线程内执行
# 没有创建新的操作系统线程

async def main():
    # 创建两个 Task
    task1 = asyncio.create_task(task_a())
    task2 = asyncio.create_task(task_b())
    
    # 两个 Task 在同一线程内协作执行
    await asyncio.gather(task1, task2)
```

### 4.2 协作式调度

```python
# 协程主动让出控制权，不是被强制切换

async def task_a():
    print("Step 1")     # ← 不会切换，继续执行
    print("Step 2")     # ← 不会切换，继续执行
    await asyncio.sleep(1)  # ← 只有这里会让出控制权
    print("Step 3")     # ← 1秒后恢复执行
```

### 4.3 状态管理

```python
task = asyncio.create_task(coro)

# 查询状态
task.done()      # 是否完成
task.cancelled()  # 是否被取消

# 获取结果
result = await task

# 取消任务
task.cancel()
```

---

## 五、协程 vs 线程对比

| 对比项 | 协程 (Task) | 线程 |
|--------|-------------|------|
| **调度者** | 事件循环 | 操作系统 |
| **切换时机** | 协程主动 `await` | 时间片到了强制切换 |
| **切换方式** | 协作式 | 抢占式 |
| **创建开销** | 极低 (~1μs) | 高 (~10μs) |
| **切换开销** | 极低 (函数调用) | 高 (上下文切换) |
| **资源占用** | 极小 (几字节) | 每个线程栈 1-8MB |
| **并发数量** | 数十万级 | 数千级 |
| **共享资源** | 无需加锁 | 需要加锁 |
| **执行环境** | 单线程内 | 多线程 |

---

## 六、关键概念总结

### 6.1 就绪队列 vs 等待队列

```
┌─────────────────────────────────────────────────────────────┐
│                    事件循环队列                              │
│                                                             │
│  就绪队列 (_ready_queue):                                   │
│  - 存放可立即执行的 Task                                    │
│  - create_task() 添加的 Task                               │
│  - 等待事件完成后恢复的 Task                                │
│                                                             │
│  等待队列 (_waiting_queue):                                 │
│  - 存放等待特定事件的 Task                                  │
│  - 按触发时间排序                                          │
│  - 到期后移到就绪队列                                      │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 Task 状态机

```
READY ──→ RUNNING ──→ FINISHED
  │           │
  │           └──→ WAITING ──→ READY
  │                              │
  └──────────────────────────────┘
```

### 6.3 create_task 执行步骤

1. 将协程包装成 Task 对象
2. 将 Task 添加到就绪队列
3. 立即返回 Task 对象
4. 事件循环调度执行 Task
5. Task 执行到 await 点时挂起
6. 等待的事件完成后恢复 Task
7. Task 执行完毕，设置结果

---

## 七、实际应用场景

### 7.1 后台日志写入

```python
class Logger:
    async def start(self):
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._writer())
    
    def log(self, message):
        self._queue.put_nowait(message)
    
    async def _writer(self):
        while True:
            message = await self._queue.get()
            with open("log.txt", "a") as f:
                f.write(message + "\n")
    
    async def stop(self):
        await self._queue.join()
        self._task.cancel()
```

### 7.2 定时心跳

```python
async def heartbeat():
    while True:
        await send_heartbeat()
        await asyncio.sleep(30)

heartbeat_task = asyncio.create_task(heartbeat())
```

### 7.3 并发请求

```python
async def fetch_all(urls):
    tasks = [asyncio.create_task(fetch(url)) for url in urls]
    results = await asyncio.gather(*tasks)
    return results
```

---

## 八、注意事项

1. **Task 是一次性的**：创建后立即开始运行，不能重复使用
2. **需要取消**：后台任务需要主动 `cancel()`，否则会一直运行
3. **异常处理**：Task 中的异常如果不 await 会被吞掉
4. **事件循环**：必须在事件循环运行时创建 Task
5. **不要阻塞**：协程内不要使用同步阻塞操作（如 `time.sleep`）

---

## 九、一句话总结

> **`asyncio.create_task` 是把协程包装成 Task 对象，添加到事件循环的就绪队列，由事件循环在同一线程内协作式调度执行，实现单线程内的高并发能力！**