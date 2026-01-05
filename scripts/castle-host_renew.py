#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Castle-Host 服务器自动续约脚本 (修复版 v2)
正确解析API响应，识别24小时冷却限制
"""

import os
import asyncio
import aiohttp
import re
import json
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse
from playwright.async_api import async_playwright
import sys

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('castle_renew.log')
    ]
)
logger = logging.getLogger(__name__)

# 存储续约前后的时间
renewal_data = {
    "server_id": "",
    "before_expiry": "",
    "after_expiry": "",
    "renewal_time": "",
    "success": False,
    "status": "",  # 新增：状态类型
    "error_message": ""
}

# ------------------ Telegram 通知 ------------------
async def tg_notify(message: str, token=None, chat_id=None):
    """发送Telegram通知"""
    if not token or not chat_id:
        token = os.environ.get("TG_BOT_TOKEN")
        chat_id = os.environ.get("TG_CHAT_ID")
        
    if not token or not chat_id:
        logger.info("ℹ️ Telegram通知未配置")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            data = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            async with session.post(url, json=data, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("✅ Telegram通知已发送")
                    return True
                else:
                    logger.warning(f"⚠️ Telegram通知发送失败: {resp.status}")
                    return False
    except Exception as e:
        logger.error(f"⚠️ TG通知失败: {e}")
        return False

# ------------------ Cookie 解析 ------------------
def parse_cookie_string(cookie_str: str):
    """解析Cookie字符串为字典列表"""
    cookies = []
    parts = cookie_str.split(';')
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        if '=' in part:
            name, value = part.split('=', 1)
            name = name.strip()
            value = value.strip()
            
            cookie_dict = {
                "name": name,
                "value": value,
                "domain": ".castle-host.com",
                "path": "/"
            }
            cookies.append(cookie_dict)
    
    logger.info(f"✅ 成功解析 {len(cookies)} 个Cookie")
    return cookies

# ------------------ 到期时间提取 ------------------
async def extract_expiry_date(page):
    """从页面提取服务器到期时间"""
    try:
        body_text = await page.text_content('body')
        
        patterns = [
            r'Сервер действует до (\d{2}\.\d{2}\.\d{4})',
            r'Оплачено до (\d{2}\.\d{2}\.\d{4})',
            r'(\d{2}\.\d{2}\.\d{4})\s*\([^)]*\)',
            r'\b(\d{2}\.\d{2}\.\d{4})\b'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, body_text)
            if match:
                date_str = match.group(1)
                logger.info(f"📅 提取到到期时间: {date_str}")
                return date_str
        
        logger.warning("⚠️ 未找到到期时间")
        return None
        
    except Exception as e:
        logger.error(f"❌ 提取到期时间失败: {e}")
        return None

# ------------------ 服务器信息提取 ------------------
async def extract_server_info(page):
    """提取服务器详细信息"""
    info = {
        "status": "Unknown",
        "expiry_date": "Unknown",
        "server_name": "Unknown",
        "balance": "Unknown",
        "tariff": "Unknown",
        "days_until_expiry": "Unknown"
    }
    
    try:
        text_content = await page.text_content('body')
        
        # 提取状态
        if re.search(r'Сервер запущен|Server running', text_content, re.IGNORECASE):
            info["status"] = "运行中"
        elif re.search(r'Сервер остановлен|Server stopped', text_content, re.IGNORECASE):
            info["status"] = "已停止"
        
        # 提取到期时间
        expiry_date = await extract_expiry_date(page)
        if expiry_date:
            info["expiry_date"] = expiry_date
            
            # 计算剩余天数
            try:
                exp_date = datetime.strptime(expiry_date, '%d.%m.%Y')
                days_left = (exp_date - datetime.now()).days
                info["days_until_expiry"] = str(days_left)
            except:
                pass
        
        # 提取余额
        balance_match = re.search(r'(\d+\.\d+)\s*₽', text_content)
        if balance_match:
            info["balance"] = balance_match.group(1)
        
        # 提取套餐
        if re.search(r'Бесплатный|Бесплатно|Free', text_content, re.IGNORECASE):
            info["tariff"] = "免费"
        else:
            info["tariff"] = "付费"
        
        logger.info(f"📊 服务器信息: 状态={info['status']}, 到期={info['expiry_date']}, 剩余天数={info['days_until_expiry']}")
        
    except Exception as e:
        logger.error(f"⚠️ 提取服务器信息失败: {e}")
    
    return info

# ------------------ 日期工具函数 ------------------
def parse_date(date_str):
    """解析日期字符串为datetime对象"""
    try:
        formats = ['%d.%m.%Y', '%Y年%m月%d日', '%Y-%m-%d']
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
    except:
        return None

def calculate_date_difference(date1_str, date2_str):
    """计算两个日期之间的天数差"""
    date1 = parse_date(date1_str)
    date2 = parse_date(date2_str)
    if date1 and date2:
        return (date2 - date1).days
    return None

# ------------------ 解析俄语错误信息 ------------------
def decode_unicode_error(error_str):
    """解码Unicode转义的俄语错误信息"""
    try:
        # 如果已经是正常字符串，直接返回
        if not error_str.startswith('\\u'):
            return error_str
        # 解码Unicode转义
        return error_str.encode('utf-8').decode('unicode_escape')
    except:
        return error_str

def analyze_error_message(error_msg):
    """分析错误信息，返回错误类型和中文描述"""
    error_lower = error_msg.lower()
    
    # 24小时限制
    if '24 час' in error_lower or '24 hour' in error_lower:
        return "rate_limited", "需要等待24小时后才能再次续期"
    
    # 已经续期
    if 'уже продлен' in error_lower or 'already renewed' in error_lower:
        return "already_renewed", "服务器已经续期过了"
    
    # 余额不足
    if 'недостаточно' in error_lower or 'insufficient' in error_lower:
        return "insufficient_funds", "账户余额不足"
    
    # 达到最大期限
    if 'максимальн' in error_lower or 'maximum' in error_lower:
        return "max_period", "已达到最大续期期限"
    
    # VK验证
    if 'vk' in error_lower or 'вк' in error_lower:
        return "vk_required", "需要VK群组验证"
    
    # 未知错误
    return "unknown", error_msg

# ------------------ 续约执行 (修复版) ------------------
async def perform_renewal(page, server_id):
    """执行续约操作（正确解析API响应）"""
    logger.info(f"🔄 开始续约流程，服务器ID: {server_id}")
    
    # 存储API响应
    api_response = {"status": None, "body": None}
    
    try:
        # 查找续约按钮
        renew_button_selectors = [
            '#freebtn',
            'button:has-text("Продлить")',
            'button:has-text("продлить")',
            'button[onclick*="freePay"]'
        ]
        
        for selector in renew_button_selectors:
            button = page.locator(selector)
            if await button.count() > 0:
                logger.info(f"🖱️ 找到续约按钮: {selector}")
                
                # 检查按钮是否禁用
                is_disabled = await button.get_attribute("disabled")
                if is_disabled:
                    logger.error("❌ 续约按钮已禁用")
                    return {"success": False, "error_type": "button_disabled", "message": "续约按钮已禁用"}
                
                # 监听API响应
                async def handle_response(response):
                    if "/buy_months/" in response.url:
                        api_response["status"] = response.status
                        try:
                            api_response["body"] = await response.json()
                            logger.info(f"📡 API响应: {json.dumps(api_response['body'], ensure_ascii=False)}")
                        except:
                            try:
                                api_response["body"] = await response.text()
                                logger.info(f"📡 API响应(文本): {api_response['body']}")
                            except:
                                pass
                
                page.on("response", handle_response)
                
                # 点击按钮
                await button.click()
                logger.info("🖱️ 已点击续约按钮")
                
                # 等待API响应
                for _ in range(20):  # 最多等待10秒
                    if api_response["body"] is not None:
                        break
                    await asyncio.sleep(0.5)
                
                # 解析API响应
                if api_response["body"]:
                    body = api_response["body"]
                    
                    # 如果是字典（JSON响应）
                    if isinstance(body, dict):
                        status = body.get("status", "")
                        
                        if status == "error":
                            error_msg = body.get("error", "未知错误")
                            error_type, error_desc = analyze_error_message(error_msg)
                            
                            logger.warning(f"⚠️ 服务器返回错误: {error_msg}")
                            logger.info(f"📋 错误类型: {error_type} - {error_desc}")
                            
                            return {
                                "success": False, 
                                "error_type": error_type, 
                                "message": error_desc,
                                "original_error": error_msg
                            }
                        
                        elif status == "success" or status == "ok":
                            logger.info("✅ 服务器确认续期成功!")
                            return {"success": True, "error_type": None, "message": "续期成功"}
                        
                        else:
                            # 未知状态，检查是否有成功指示
                            if body.get("success") or body.get("renewed"):
                                return {"success": True, "error_type": None, "message": "续期成功"}
                            else:
                                return {"success": False, "error_type": "unknown_response", "message": f"未知响应: {body}"}
                    
                    # 如果是字符串响应
                    elif isinstance(body, str):
                        if "error" in body.lower() or "ошибка" in body.lower():
                            error_type, error_desc = analyze_error_message(body)
                            return {"success": False, "error_type": error_type, "message": error_desc}
                        elif "success" in body.lower() or "успех" in body.lower():
                            return {"success": True, "error_type": None, "message": "续期成功"}
                
                # 如果没有捕获到API响应，等待页面更新后检查
                await page.wait_for_timeout(3000)
                
                # 检查页面上是否有成功/错误提示
                page_text = await page.text_content('body')
                
                # 检查24小时限制
                if '24 час' in page_text:
                    return {
                        "success": False,
                        "error_type": "rate_limited",
                        "message": "需要等待24小时后才能再次续期"
                    }
                
                # 检查成功提示
                if re.search(r'Сервер продлен|продлен успешно|успешно продлен', page_text, re.IGNORECASE):
                    return {"success": True, "error_type": None, "message": "续期成功"}
                
                # 无法确定结果
                logger.warning("⚠️ 无法确定续约结果，需要验证到期时间")
                return {"success": None, "error_type": "unknown", "message": "需要验证到期时间"}
        
        # 未找到按钮，尝试JavaScript
        logger.warning("⚠️ 未找到续约按钮，尝试JavaScript调用")
        
        try:
            result = await page.evaluate("typeof freePay === 'function' ? (freePay(), true) : false")
            if result:
                logger.info("✅ 通过JavaScript调用freePay函数")
                await page.wait_for_timeout(3000)
                return {"success": None, "error_type": None, "message": "JavaScript调用完成，需要验证"}
        except Exception as e:
            logger.error(f"❌ JavaScript调用失败: {e}")
        
        return {"success": False, "error_type": "no_button", "message": "未找到续约按钮"}
        
    except Exception as e:
        logger.error(f"❌ 续约过程出错: {e}")
        return {"success": False, "error_type": "exception", "message": str(e)}

# ------------------ 验证续约结果 ------------------
async def verify_renewal(page, original_expiry):
    """验证续约是否成功"""
    try:
        await asyncio.sleep(2)
        await page.reload(wait_until="networkidle")
        await asyncio.sleep(2)
        
        new_expiry = await extract_expiry_date(page)
        
        if not new_expiry:
            logger.warning("⚠️ 无法获取续约后的到期时间")
            return None, 0
        
        logger.info(f"📅 续约前到期时间: {original_expiry}")
        logger.info(f"📅 续约后到期时间: {new_expiry}")
        
        if original_expiry and new_expiry:
            days_added = calculate_date_difference(original_expiry, new_expiry)
            if days_added is not None:
                logger.info(f"📊 续期增加了 {days_added} 天")
                return new_expiry, days_added
        
        return new_expiry, 0
        
    except Exception as e:
        logger.error(f"❌ 验证续约结果失败: {e}")
        return None, 0

# ------------------ 主函数 ------------------
async def main():
    """主执行函数"""
    logger.info("=" * 60)
    logger.info("Castle-Host 服务器自动续约脚本 (修复版 v2)")
    logger.info("正确解析API响应，识别24小时冷却限制")
    logger.info("=" * 60)
    
    # 获取环境变量
    cookie_str = os.environ.get("CASTLE_COOKIES", "").strip()
    server_id = os.environ.get("SERVER_ID", "117954")
    tg_token = os.environ.get("TG_BOT_TOKEN")
    tg_chat_id = os.environ.get("TG_CHAT_ID")
    
    # 新增：是否强制续期（即使剩余天数较多）
    force_renew = os.environ.get("FORCE_RENEW", "false").lower() == "true"
    # 新增：剩余多少天内才自动续期
    renew_threshold = int(os.environ.get("RENEW_THRESHOLD", "3"))
    
    if not cookie_str:
        error_msg = "❌ 错误：未设置 CASTLE_COOKIES 环境变量"
        logger.error(error_msg)
        await tg_notify(error_msg, tg_token, tg_chat_id)
        return
    
    # 初始化续约数据
    renewal_data["server_id"] = server_id
    renewal_data["renewal_time"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 解析Cookie
    cookies = parse_cookie_string(cookie_str)
    if not cookies:
        error_msg = "❌ 错误：无法解析Cookie字符串"
        logger.error(error_msg)
        await tg_notify(error_msg, tg_token, tg_chat_id)
        return
    
    server_url = f"https://cp.castle-host.com/servers/pay/index/{server_id}"
    
    logger.info("🚀 启动浏览器...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-dev-shm-usage']
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        
        await context.add_cookies(cookies)
        logger.info("✅ Cookie已添加到浏览器")
        
        page = await context.new_page()
        page.set_default_timeout(60000)
        
        try:
            logger.info(f"🌐 访问服务器页面: {server_url}")
            await page.goto(server_url, wait_until="networkidle")
            
            # 检查登录状态
            if "login" in page.url or "auth" in page.url:
                error_msg = "❌ Cookie失效，无法登录"
                logger.error(error_msg)
                await page.screenshot(path="login_failed.png", full_page=True)
                await tg_notify(error_msg, tg_token, tg_chat_id)
                return
            
            logger.info("✅ 登录成功")
            
            # 提取服务器信息
            server_info = await extract_server_info(page)
            original_expiry = server_info.get("expiry_date", "Unknown")
            renewal_data["before_expiry"] = original_expiry
            
            # 检查是否需要续约
            days_left = server_info.get("days_until_expiry", "Unknown")
            skip_renewal = False
            
            if days_left != "Unknown":
                try:
                    days = int(days_left)
                    if days > renew_threshold and not force_renew:
                        logger.info(f"ℹ️ 距离到期还有 {days} 天 (阈值: {renew_threshold} 天)")
                        logger.info("ℹ️ 跳过续约，如需强制续约请设置 FORCE_RENEW=true")
                        skip_renewal = True
                        
                        # 发送状态通知
                        message = f"""ℹ️ Castle-Host 服务器状态检查

🆔 服务器ID: {server_id}
📊 当前状态: {server_info.get('status', 'Unknown')}
📅 到期时间: {original_expiry}
⏳ 剩余天数: {days} 天
💰 账户余额: {server_info.get('balance', 'Unknown')} ₽

📝 无需续期，距离到期还有 {days} 天
🔗 管理页面: {server_url}"""
                        
                        await tg_notify(message, tg_token, tg_chat_id)
                        renewal_data["success"] = True
                        renewal_data["status"] = "skipped"
                        renewal_data["after_expiry"] = original_expiry
                        
                except ValueError:
                    pass
            
            if not skip_renewal:
                # 执行续约
                renewal_result = await perform_renewal(page, server_id)
                
                renewal_data["status"] = renewal_result.get("error_type", "unknown")
                
                # 根据结果处理
                if renewal_result["success"] == True:
                    # 明确成功
                    new_expiry, days_added = await verify_renewal(page, original_expiry)
                    renewal_data["after_expiry"] = new_expiry if new_expiry else "Unknown"
                    renewal_data["success"] = True
                    
                    message = f"""✅ Castle-Host 服务器续约成功！

🆔 服务器ID: {server_id}
📊 当前状态: {server_info.get('status', 'Unknown')}
📅 续约前到期: {original_expiry}
📅 续约后到期: {new_expiry if new_expiry else 'Unknown'}
📈 续期增加: {days_added} 天
💰 账户余额: {server_info.get('balance', 'Unknown')} ₽
⏰ 续约时间: {renewal_data['renewal_time']}
🔗 管理页面: {server_url}"""
                    
                    logger.info("🎉 续约成功！")
                    
                elif renewal_result["success"] == False:
                    # 明确失败
                    error_type = renewal_result.get("error_type", "unknown")
                    error_msg = renewal_result.get("message", "未知错误")
                    original_error = renewal_result.get("original_error", "")
                    
                    renewal_data["success"] = False
                    renewal_data["after_expiry"] = original_expiry
                    renewal_data["error_message"] = error_msg
                    
                    # 根据错误类型选择不同的图标和处理方式
                    if error_type == "rate_limited":
                        icon = "⏰"
                        title = "Castle-Host 续约冷却中"
                        suggestion = "这是正常的限制，无需担心。脚本会在下次运行时重试。"
                    elif error_type == "already_renewed":
                        icon = "✅"
                        title = "Castle-Host 已经续期过了"
                        suggestion = "服务器已在有效期内，无需重复续期。"
                    elif error_type == "max_period":
                        icon = "📅"
                        title = "Castle-Host 达到最大续期期限"
                        suggestion = "已达到免费续期的最大天数限制。"
                    else:
                        icon = "⚠️"
                        title = "Castle-Host 续约失败"
                        suggestion = "请检查Cookie是否有效，或手动登录网站查看。"
                    
                    message = f"""{icon} {title}

🆔 服务器ID: {server_id}
📊 当前状态: {server_info.get('status', 'Unknown')}
📅 当前到期: {original_expiry}
⏳ 剩余天数: {days_left} 天
💰 账户余额: {server_info.get('balance', 'Unknown')} ₽
⏰ 操作时间: {renewal_data['renewal_time']}

❌ 错误类型: {error_type}
📋 错误信息: {error_msg}
{f'🔤 原始错误: {original_error}' if original_error and original_error != error_msg else ''}

💡 {suggestion}
🔗 管理页面: {server_url}"""
                    
                    if error_type == "rate_limited":
                        logger.info("⏰ 24小时冷却限制，这是正常的")
                    else:
                        logger.error(f"❌ 续约失败: {error_msg}")
                    
                else:
                    # 结果不确定，需要验证
                    new_expiry, days_added = await verify_renewal(page, original_expiry)
                    renewal_data["after_expiry"] = new_expiry if new_expiry else "Unknown"
                    
                    if new_expiry and new_expiry != original_expiry and days_added > 0:
                        renewal_data["success"] = True
                        message = f"""✅ Castle-Host 服务器续约成功！

🆔 服务器ID: {server_id}
📅 续约前到期: {original_expiry}
📅 续约后到期: {new_expiry}
📈 续期增加: {days_added} 天
⏰ 续约时间: {renewal_data['renewal_time']}
🔗 管理页面: {server_url}"""
                        logger.info("🎉 续约成功（通过日期验证确认）！")
                    else:
                        renewal_data["success"] = False
                        renewal_data["error_message"] = "到期时间未变化"
                        message = f"""⚠️ Castle-Host 续约结果不确定

🆔 服务器ID: {server_id}
📅 到期时间: {original_expiry}
⏰ 操作时间: {renewal_data['renewal_time']}

📋 说明: 到期时间未发生变化，可能已经续期过了
🔗 管理页面: {server_url}"""
                        logger.warning("⚠️ 续约结果不确定")
                
                await tg_notify(message, tg_token, tg_chat_id)
            
            # 保存记录
            with open("renewal_history.json", "a", encoding="utf-8") as f:
                json.dump(renewal_data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            logger.info("💾 续约记录已保存")
            
            # 截图
            await page.screenshot(path="renewal_result.png", full_page=True)
            logger.info("📸 结果截图已保存")
            
        except Exception as e:
            error_msg = f"❌ 脚本执行错误: {str(e)}"
            logger.error(error_msg, exc_info=True)
            renewal_data["success"] = False
            renewal_data["error_message"] = str(e)
            
            try:
                await page.screenshot(path="error.png", full_page=True)
            except:
                pass
            
            await tg_notify(error_msg, tg_token, tg_chat_id)
            
        finally:
            await context.close()
            await browser.close()
            logger.info("👋 浏览器已关闭")
            
            # 总结
            logger.info("=" * 60)
            logger.info("续约结果总结:")
            logger.info(f"  服务器ID: {renewal_data['server_id']}")
            logger.info(f"  状态类型: {renewal_data.get('status', 'unknown')}")
            logger.info(f"  续约前到期: {renewal_data['before_expiry']}")
            logger.info(f"  续约后到期: {renewal_data['after_expiry']}")
            logger.info(f"  是否成功: {'✅ 是' if renewal_data['success'] else '❌ 否'}")
            if renewal_data.get('error_message'):
                logger.info(f"  错误信息: {renewal_data['error_message']}")
            logger.info("=" * 60)

# ------------------ 入口点 ------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Castle-Host 服务器自动续约脚本 (修复版 v2)")
    print("正确解析API响应，识别24小时冷却限制")
    print("=" * 60)
    
    cookie_str = os.environ.get("CASTLE_COOKIES", "").strip()
    
    if not cookie_str:
        print("❌ 错误：未设置 CASTLE_COOKIES 环境变量")
        print()
        print("💡 使用方法：")
        print("   export CASTLE_COOKIES=\"PHPSESSID=xxx; uid=xxx\"")
        print("   python castle_renew_v2.py")
        print()
        print("📌 可选环境变量：")
        print("   SERVER_ID      - 服务器ID (默认: 117954)")
        print("   RENEW_THRESHOLD - 剩余多少天内才续期 (默认: 3)")
        print("   FORCE_RENEW    - 强制续期 (true/false, 默认: false)")
        print("   TG_BOT_TOKEN   - Telegram机器人Token")
        print("   TG_CHAT_ID     - Telegram聊天ID")
        sys.exit(1)
    
    asyncio.run(main())
