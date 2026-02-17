from astrbot.core import Star, Event
import re
import asyncio
import datetime
import pytz
import json
import os

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
        
    async def on_load(self):
        """插件加载时执行"""
        self.logger.info("定时任务提醒插件加载成功")
        if self.config["enabled"]:
            await self.load_reminders()
            await self.start_check_task()
    
    async def on_unload(self):
        """插件卸载时执行"""
        self.logger.info("定时任务提醒插件卸载")
        if self.check_task:
            self.check_task.cancel()
        await self.save_reminders()
    
    async def load_reminders(self):
        """加载保存的提醒任务"""
        try:
            reminder_path = os.path.join(self.data_dir, self.config["reminder_file"])
            if os.path.exists(reminder_path):
                with open(reminder_path, 'r', encoding='utf-8') as f:
                    self.reminders = json.load(f)
                # 过滤掉已过期的提醒
                self.reminders = [r for r in self.reminders if r["timestamp"] > datetime.datetime.now(self.tz).timestamp()]
                self.logger.info(f"加载了 {len(self.reminders)} 个提醒任务")
        except Exception as e:
            self.logger.error(f"加载提醒任务失败: {e}")
            self.reminders = []
    
    async def save_reminders(self):
        """保存提醒任务"""
        try:
            reminder_path = os.path.join(self.data_dir, self.config["reminder_file"])
            os.makedirs(os.path.dirname(reminder_path), exist_ok=True)
            with open(reminder_path, 'w', encoding='utf-8') as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=2)
            self.logger.info(f"保存了 {len(self.reminders)} 个提醒任务")
        except Exception as e:
            self.logger.error(f"保存提醒任务失败: {e}")
    
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
        
        for reminder in self.reminders:
            if reminder["timestamp"] <= current_time:
                # 发送提醒
                await self.send_reminder(reminder)
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
            self.logger.info(f"发送提醒: {reminder['task']}")
        except Exception as e:
            self.logger.error(f"发送提醒失败: {e}")
    
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
            (r'今天\s*(\d{1,2}):(\d{2})\s*(.*)', lambda m: (0, int(m.group(1)), int(m.group(2)), m.group(3))),
            # 明天 12:30
            (r'明天\s*(\d{1,2}):(\d{2})\s*(.*)', lambda m: (1, int(m.group(1)), int(m.group(2)), m.group(3))),
            # 后天 12:30
            (r'后天\s*(\d{1,2}):(\d{2})\s*(.*)', lambda m: (2, int(m.group(1)), int(m.group(2)), m.group(3))),
            # 周X 12:30
            (r'周([一二三四五六日])\s*(\d{1,2}):(\d{2})\s*(.*)', self._parse_weekday),
            # X月X日 12:30
            (r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})\s*(.*)', lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5))),
            # X天后 12:30
            (r'(\d+)天后\s*(\d{1,2}):(\d{2})\s*(.*)', lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4))),
            # 提醒我 X分钟后 ...
            (r'提醒我\s*(\d+)分钟后\s*(.*)', lambda m: (0, 0, int(m.group(1)), m.group(2), 'minutes')),
            # 提醒我 X小时后 ...
            (r'提醒我\s*(\d+)小时后\s*(.*)', lambda m: (0, int(m.group(1)), 0, m.group(2), 'hours')),
            # 提醒我 X天后 ...
            (r'提醒我\s*(\d+)天后\s*(.*)', lambda m: (int(m.group(1)), 0, 0, m.group(2), 'days')),
        ]
        
        for pattern, parser in time_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                try:
                    result = parser(match)
                    reminder_time = await self.calculate_reminder_time(result)
                    if reminder_time:
                        task = result[-1] if isinstance(result[-1], str) else result[-2]
                        await self.add_reminder(reminder_time, task, event)
                        return True
                except Exception as e:
                    self.logger.error(f"解析提醒时间失败: {e}")
        
        return False
    
    def _parse_weekday(self, match):
        """解析周X格式"""
        weekday_map = {'日': 6, '一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5}
        weekday = weekday_map.get(match.group(1), 0)
        hour = int(match.group(2))
        minute = int(match.group(3))
        task = match.group(4)
        return (weekday, hour, minute, task)
    
    async def calculate_reminder_time(self, parsed_result) -> datetime.datetime:
        """计算提醒时间"""
        now = datetime.datetime.now(self.tz)
        
        if len(parsed_result) == 4 and parsed_result[0] in [0, 1, 2]:
            # 今天/明天/后天
            days, hour, minute, task = parsed_result
            reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            reminder_time += datetime.timedelta(days=days)
        
        elif len(parsed_result) == 4 and isinstance(parsed_result[0], int) and parsed_result[0] > 2:
            # X天后
            days, hour, minute, task = parsed_result
            reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            reminder_time += datetime.timedelta(days=days)
        
        elif len(parsed_result) == 5 and isinstance(parsed_result[0], int) and parsed_result[0] <= 12:
            # 月日格式
            month, day, hour, minute, task = parsed_result
            year = now.year
            # 处理跨年情况
            if month < now.month or (month == now.month and day < now.day):
                year += 1
            reminder_time = now.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
        
        elif len(parsed_result) == 4 and parsed_result[-1] == 'minutes':
            # 分钟后
            _, _, minutes, task = parsed_result
            reminder_time = now + datetime.timedelta(minutes=minutes)
        
        elif len(parsed_result) == 4 and parsed_result[-1] == 'hours':
            # 小时后
            _, hours, _, task = parsed_result
            reminder_time = now + datetime.timedelta(hours=hours)
        
        elif len(parsed_result) == 4 and parsed_result[-1] == 'days':
            # 天后
            days, _, _, task = parsed_result
            reminder_time = now + datetime.timedelta(days=days)
        
        elif len(parsed_result) == 4:
            # 周X格式
            target_weekday, hour, minute, task = parsed_result
            current_weekday = now.weekday()
            days_to_add = (target_weekday - current_weekday) % 7
            if days_to_add == 0 and (now.hour > hour or (now.hour == hour and now.minute >= minute)):
                days_to_add = 7
            reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            reminder_time += datetime.timedelta(days=days_to_add)
        
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
            "id": f"{event.user_id}_{int(reminder_time.timestamp())}",
            "timestamp": reminder_time.timestamp(),
            "task": task,
            "user_id": event.user_id,
            "platform": event.platform,
            "created_at": datetime.datetime.now(self.tz).timestamp()
        }
        
        # 添加到提醒列表
        self.reminders.append(reminder)
        await self.save_reminders()
        
        # 回复用户
        time_str = reminder_time.strftime("%Y年%m月%d日 %H:%M")
        await event.reply(f"已设置提醒: {task}\n时间: {time_str}")
        self.logger.info(f"添加提醒: {task} 时间: {time_str} 用户: {event.user_id}")

# 导出插件实例
export = ReminderPlugin()