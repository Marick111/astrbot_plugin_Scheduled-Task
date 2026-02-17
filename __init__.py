from astrbot.core import Star, Event
from astrbot.api import logger
from astrbot.api.star import StarTools
import re
import asyncio
import datetime
import pytz
import json
from pathlib import Path

class ReminderPlugin(Star):
    def __init__(self):
        super().__init__()
        self.name = "reminder"
        self.description = "定时任务提醒插件"
        self.version = "1.0.0"
        self.author = "AstrBot Team"
        
        # 配置
        self.config = {
            "enabled": True,
            "timezone": "Asia/Shanghai",
            "reminder_file": "reminders.json",
            "check_interval": 60  # 秒
        }
        
        # 存储提醒任务
        self.reminders = []
        # 定时器任务
        self.check_task = None
        # 时区对象
        self.tz = pytz.timezone(self.config["timezone"])
        # 数据目录
        self.data_dir = StarTools.get_data_dir()
        # 锁
        self.lock = asyncio.Lock()
    
    async def on_load(self):
        """插件加载时执行"""
        logger.info("定时任务提醒插件加载成功")
        if self.config["enabled"]:
            await self.load_reminders()
            await self.start_check_task()
    
    async def on_unload(self):
        """插件卸载时执行"""
        logger.info("定时任务提醒插件卸载")
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                pass
        await self.save_reminders()
    
    async def load_reminders(self):
        """加载保存的提醒任务"""
        try:
            reminder_path = self.data_dir / self.config["reminder_file"]
            if reminder_path.exists():
                with open(reminder_path, 'r', encoding='utf-8') as f:
                    self.reminders = json.load(f)
                # 过滤掉已过期的提醒
                self.reminders = [r for r in self.reminders if r["timestamp"] > datetime.datetime.now(self.tz).timestamp()]
                logger.info(f"加载了 {len(self.reminders)} 个提醒任务")
        except Exception as e:
            logger.error(f"加载提醒任务失败: {e}")
            self.reminders = []
    
    async def save_reminders(self):
        """保存提醒任务"""
        try:
            reminder_path = self.data_dir / self.config["reminder_file"]
            reminder_path.parent.mkdir(parents=True, exist_ok=True)
            with open(reminder_path, 'w', encoding='utf-8') as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=2)
            logger.info(f"保存了 {len(self.reminders)} 个提醒任务")
        except Exception as e:
            logger.error(f"保存提醒任务失败: {e}")
    
    async def start_check_task(self):
        """启动检查任务"""
        async def check_reminders():
            while True:
                try:
                    await self.check_pending_reminders()
                    await asyncio.sleep(self.config["check_interval"])
                except Exception as e:
                    self.logger.error(f"检查提醒任务失败: {e}")
                    await asyncio.sleep(self.config["check_interval"])
        
        self.check_task = asyncio.create_task(check_reminders())
    
    async def check_pending_reminders(self):
        """检查并执行待处理的提醒"""
        current_time = datetime.datetime.now(self.tz).timestamp()
        pending_reminders = []
        
        async with self.lock:
            for reminder in self.reminders:
                if reminder["timestamp"] <= current_time:
                    # 发送提醒
                    success = await self.send_reminder(reminder)
                    # 只有发送成功才移除提醒
                    if not success:
                        pending_reminders.append(reminder)
                else:
                    pending_reminders.append(reminder)
            
            if len(self.reminders) != len(pending_reminders):
                self.reminders = pending_reminders
                await self.save_reminders()
    
    async def send_reminder(self, reminder):
        """发送提醒消息"""
        try:
            message = f"⏰ 提醒: {reminder['task']}"
            if reminder.get('user_id') and reminder.get('platform'):
                await self.bot.send_message(
                    platform=reminder['platform'],
                    user_id=reminder['user_id'],
                    content=message
                )
            logger.info(f"发送提醒: {reminder['task']}")
        except Exception as e:
            logger.error(f"发送提醒失败: {e}")
            # 发送失败时不移除提醒，等待下次重试
            return False
        return True
    
    async def on_message(self, event: Event):
        """处理来自AstrBot的消息"""
        if not self.config["enabled"]:
            return
        
        content = event.content
        
        # 检查是否包含提醒相关内容
        if await self.extract_reminder_info(content, event):
            # 已经处理了提醒，不需要其他处理
            return
    
    async def extract_reminder_info(self, content: str, event: Event) -> bool:
        """从消息中提取提醒信息"""
        # 匹配时间格式的正则表达式
        time_patterns = [
            # 今天 12:30
            (r'^.*今天\s*(\d{1,2}):(\d{2})\s*(.*)$', lambda m: ('today', int(m.group(1)), int(m.group(2)), m.group(3))),
            # 明天 12:30
            (r'^.*明天\s*(\d{1,2}):(\d{2})\s*(.*)$', lambda m: ('tomorrow', int(m.group(1)), int(m.group(2)), m.group(3))),
            # 后天 12:30
            (r'^.*后天\s*(\d{1,2}):(\d{2})\s*(.*)$', lambda m: ('day_after_tomorrow', int(m.group(1)), int(m.group(2)), m.group(3))),
            # 周X 12:30
            (r'^.*周([一二三四五六日])\s*(\d{1,2}):(\d{2})\s*(.*)$', self._parse_weekday),
            # X月X日 12:30
            (r'^.*(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})\s*(.*)$', lambda m: ('date', int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5))),
            # X天后 12:30
            (r'^.*(\d+)天后\s*(\d{1,2}):(\d{2})\s*(.*)$', lambda m: ('days_later', int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4))),
            # 提醒我 X分钟后 ...
            (r'^.*提醒我\s*(\d+)分钟后\s*(.*)$', lambda m: ('minutes_later', int(m.group(1)), m.group(2))),
            # 提醒我 X小时后 ...
            (r'^.*提醒我\s*(\d+)小时后\s*(.*)$', lambda m: ('hours_later', int(m.group(1)), m.group(2))),
            # 提醒我 X天后 ...
            (r'^.*提醒我\s*(\d+)天后\s*(.*)$', lambda m: ('days_later_simple', int(m.group(1)), m.group(2))),
        ]
        
        for pattern, parser in time_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                try:
                    result = parser(match)
                    reminder_time = await self.calculate_reminder_time(result)
                    if reminder_time:
                        # 提取任务内容
                        if result[0] == 'minutes_later' or result[0] == 'hours_later' or result[0] == 'days_later_simple':
                            task = result[2]
                        elif result[0] == 'date':
                            task = result[5]
                        elif result[0] == 'days_later':
                            task = result[4]
                        else:
                            task = result[3]
                        await self.add_reminder(reminder_time, task, event)
                        return True
                except Exception as e:
                    logger.error(f"解析提醒时间失败: {e}")
        
        return False
    
    def _parse_weekday(self, match):
        """解析周X格式"""
        weekday_map = {'日': 6, '一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5}
        weekday = weekday_map.get(match.group(1), 0)
        hour = int(match.group(2))
        minute = int(match.group(3))
        task = match.group(4)
        return ('weekday', weekday, hour, minute, task)
    
    async def calculate_reminder_time(self, parsed_result) -> datetime.datetime:
        """计算提醒时间"""
        now = datetime.datetime.now(self.tz)
        
        if parsed_result[0] == 'today':
            # 今天
            _, hour, minute, task = parsed_result
            reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            # 如果时间已过，设为明天
            if reminder_time <= now:
                reminder_time += datetime.timedelta(days=1)
        
        elif parsed_result[0] == 'tomorrow':
            # 明天
            _, hour, minute, task = parsed_result
            reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            reminder_time += datetime.timedelta(days=1)
        
        elif parsed_result[0] == 'day_after_tomorrow':
            # 后天
            _, hour, minute, task = parsed_result
            reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            reminder_time += datetime.timedelta(days=2)
        
        elif parsed_result[0] == 'weekday':
            # 周X格式
            _, target_weekday, hour, minute, task = parsed_result
            current_weekday = now.weekday()
            days_to_add = (target_weekday - current_weekday) % 7
            if days_to_add == 0 and (now.hour > hour or (now.hour == hour and now.minute >= minute)):
                days_to_add = 7
            reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            reminder_time += datetime.timedelta(days=days_to_add)
        
        elif parsed_result[0] == 'date':
            # 月日格式
            _, month, day, hour, minute, task = parsed_result
            year = now.year
            # 处理跨年情况
            if month < now.month or (month == now.month and day < now.day):
                year += 1
            reminder_time = now.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
        
        elif parsed_result[0] == 'days_later':
            # X天后
            _, days, hour, minute, task = parsed_result
            reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            reminder_time += datetime.timedelta(days=days)
        
        elif parsed_result[0] == 'minutes_later':
            # 分钟后
            _, minutes, task = parsed_result
            reminder_time = now + datetime.timedelta(minutes=minutes)
        
        elif parsed_result[0] == 'hours_later':
            # 小时后
            _, hours, task = parsed_result
            reminder_time = now + datetime.timedelta(hours=hours)
        
        elif parsed_result[0] == 'days_later_simple':
            # 天后
            _, days, task = parsed_result
            reminder_time = now + datetime.timedelta(days=days)
        
        else:
            return None
        
        return reminder_time
    
    async def add_reminder(self, reminder_time: datetime.datetime, task: str, event: Event):
        """添加提醒任务"""
        # 验证任务内容
        task = task.strip()
        if not task:
            await event.reply("请指定提醒的内容")
            return
        
        # 检查时间是否有效
        if reminder_time <= datetime.datetime.now(self.tz):
            await event.reply("提醒时间不能早于当前时间")
            return
        
        # 创建提醒任务
        reminder = {
            "id": f"{event.user_id}_{int(reminder_time.timestamp())}_{hash(task) % 10000}",
            "timestamp": reminder_time.timestamp(),
            "task": task,
            "user_id": event.user_id,
            "platform": event.platform,
            "created_at": datetime.datetime.now(self.tz).timestamp()
        }
        
        # 添加到提醒列表（使用锁）
        async with self.lock:
            self.reminders.append(reminder)
            await self.save_reminders()
        
        # 回复用户
        time_str = reminder_time.strftime("%Y年%m月%d日 %H:%M")
        await event.reply(f"已设置提醒: {task}\n时间: {time_str}")
        logger.info(f"添加提醒: {task} 时间: {time_str} 用户: {event.user_id}")

# 导出插件实例
export = ReminderPlugin()