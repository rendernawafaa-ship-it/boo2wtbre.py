import smtplib
import time
import asyncio
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ----------------------- Settings -----------------------
BOT_TOKEN = "8342153087:AAGXNU6QKqappGvm6fvpFOdTzUVVAu5DiUE"
ADMIN_ID = "69595435"
MAX_SENDERS = 150
MAX_RETRIES = 2
EMAIL_REGEX = r"[^@]+@[^@]+\.[^@]+"

# Global storage
user_sessions = {}
stop_flags = {}
sending_status = {}
user_states = {}
last_messages = {}
sending_tasks = {}  # تخزين المهام النشطة لكل مستخدم
sending_in_progress = {}  # تتبع عمليات الإرسال النشطة

# قائمة الأدمن والمستخدمين
admins = [ADMIN_ID]
allowed_users = []

# حالة البوت
bot_paid_mode = False

def is_admin(user_id):
    return str(user_id) in admins

def is_allowed_user(user_id):
    if bot_paid_mode:
        return str(user_id) in admins or str(user_id) in allowed_users
    return True

async def delete_previous_messages(chat_id, context):
    """حذف الرسائل السابقة - فقط للرسائل العادية وليس تقارير الإرسال"""
    if chat_id in last_messages:
        messages_to_delete = []
        for msg_id in last_messages[chat_id]:
            # عدم حذف رسائل تقارير الإرسال
            if msg_id not in sending_status.get('report_messages', []):
                messages_to_delete.append(msg_id)
        
        for msg_id in messages_to_delete:
            try:
                await context.bot.delete_message(chat_id, msg_id)
            except Exception as e:
                print(f"Error deleting message: {e}")
        
        # تحديث last_messages لإزالة الرسائل المحذوفة فقط
        last_messages[chat_id] = [msg_id for msg_id in last_messages[chat_id] if msg_id not in messages_to_delete]

async def send_new_message(context, chat_id, text, reply_markup=None, parse_mode=None, is_report=False):
    """إرسال رسالة جديدة مع التحكم في الحذف"""
    # فقط عند /start نحذف الرسائل السابقة (ما عدا تقارير الإرسال)
    await delete_previous_messages(chat_id, context)
    
    if parse_mode:
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    else:
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup
        )
    
    if chat_id not in last_messages:
        last_messages[chat_id] = []
    
    # إذا كانت رسالة تقرير إرسال، لا نضيفها إلى last_messages
    if not is_report:
        last_messages[chat_id].append(message.message_id)
    
    return message

def main_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("إضافة حسابات", callback_data="manage_senders"),
         InlineKeyboardButton("تعيين المستلمين", callback_data="set_receivers")],
        [InlineKeyboardButton("تعيين المواضيع", callback_data="set_subjects"),
         InlineKeyboardButton("تعيين الرسائل", callback_data="set_bodies")],
        [InlineKeyboardButton("تعيين التأخير", callback_data="set_delay"),
         InlineKeyboardButton("تعيين العدد", callback_data="set_count")],
        [InlineKeyboardButton("خوارزميات الإرسال", callback_data="sending_algorithms"),
         InlineKeyboardButton("معلومات الإرسال", callback_data="sending_info")],
        [InlineKeyboardButton("بدء الإرسال", callback_data="start_sending"),
         InlineKeyboardButton("عرض المعلومات", callback_data="show_info")]
    ]
    
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("الإدارة", callback_data="admin_menu")])
    
    # إضافة زر التواصل مع المطور للجميع
    keyboard.append([InlineKeyboardButton("التواصل مع المطور", url="https://t.me/iioowu")])
    
    return InlineKeyboardMarkup(keyboard)

def sending_algorithms_menu():
    """قائمة خوارزميات الإرسال"""
    keyboard = [
        [InlineKeyboardButton("الإرسال المتوازي (5 في نفس الوقت)", callback_data="algorithm_parallel")],
        [InlineKeyboardButton("الإرسال المدرج (واحد تلو الآخر)", callback_data="algorithm_sequential")],
        [InlineKeyboardButton("الرجوع", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def developer_contact_menu():
    """قائمة خاصة للمستخدمين غير المسموح لهم"""
    keyboard = [
        [InlineKeyboardButton("تواصل مع المطور", url="https://t.me/iioowu")],
        [InlineKeyboardButton("المحاولة مرة أخرى", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def admin_menu():
    keyboard = [
        [InlineKeyboardButton("إضافة أدمن", callback_data="add_admin"),
         InlineKeyboardButton("حذف أدمن", callback_data="remove_admin")],
        [InlineKeyboardButton("إضافة مستخدم", callback_data="add_user"),
         InlineKeyboardButton("حذف مستخدم", callback_data="remove_user")],
        [InlineKeyboardButton("عرض الأدمن", callback_data="list_admins"),
         InlineKeyboardButton("عرض المستخدمين", callback_data="list_users")],
        [InlineKeyboardButton("وضع البوت: " + ("مدفوع" if bot_paid_mode else "مجاني"), callback_data="toggle_bot_mode")],
        [InlineKeyboardButton("الرجوع", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("الرجوع", callback_data="back_to_menu")]])

def senders_management_menu():
    keyboard = [
        [InlineKeyboardButton("إضافة حساب", callback_data="add_senders")],
        [InlineKeyboardButton("مسح حساب", callback_data="remove_senders")],
        [InlineKeyboardButton("تعيين توقيت فردي", callback_data="set_individual_timing")],
        [InlineKeyboardButton("الرجوع", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def remove_senders_menu(senders):
    keyboard = []
    for sender in senders:
        email_only = sender['email'].split(':')[0] if ':' in sender['email'] else sender['email']
        keyboard.append([InlineKeyboardButton(f"حذف {email_only}", callback_data=f"remove_sender:{email_only}")])
    
    keyboard.append([InlineKeyboardButton("الرجوع", callback_data="manage_senders")])
    return InlineKeyboardMarkup(keyboard)

def individual_timing_menu(senders):
    keyboard = []
    for sender in senders:
        email_only = sender['email'].split(':')[0] if ':' in sender['email'] else sender['email']
        current_delay = sender.get('individual_delay', 'إفتراضي')
        keyboard.append([InlineKeyboardButton(f"توقيت {email_only}: {current_delay}s", callback_data=f"set_sender_delay:{email_only}")])
    
    keyboard.append([InlineKeyboardButton("الرجوع", callback_data="manage_senders")])
    return InlineKeyboardMarkup(keyboard)

def bodies_management_menu():
    keyboard = [
        [InlineKeyboardButton("إضافة رسالة", callback_data="add_body")],
        [InlineKeyboardButton("مسح رسالة", callback_data="remove_body")],
        [InlineKeyboardButton("الرجوع", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def remove_bodies_menu(bodies):
    keyboard = []
    for i, body in enumerate(bodies):
        preview = body[:30] + "..." if len(body) > 30 else body
        keyboard.append([InlineKeyboardButton(f"مسح الرسالة {i+1}: {preview}", callback_data=f"remove_body:{i}")])
    
    keyboard.append([InlineKeyboardButton("الرجوع", callback_data="set_bodies")])
    return InlineKeyboardMarkup(keyboard)

def subjects_management_menu():
    keyboard = [
        [InlineKeyboardButton("إضافة موضوع", callback_data="add_subject")],
        [InlineKeyboardButton("مسح موضوع", callback_data="remove_subject")],
        [InlineKeyboardButton("الرجوع", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def remove_subjects_menu(subjects):
    keyboard = []
    for i, subject in enumerate(subjects):
        preview = subject[:30] + "..." if len(subject) > 30 else subject
        keyboard.append([InlineKeyboardButton(f"مسح الموضوع {i+1}: {preview}", callback_data=f"remove_subject:{i}")])
    
    keyboard.append([InlineKeyboardButton("الرجوع", callback_data="set_subjects")])
    return InlineKeyboardMarkup(keyboard)

def receivers_management_menu():
    keyboard = [
        [InlineKeyboardButton("إضافة مستلم", callback_data="add_receiver")],
        [InlineKeyboardButton("مسح مستلم", callback_data="remove_receiver")],
        [InlineKeyboardButton("الرجوع", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def remove_receivers_menu(receivers):
    keyboard = []
    for i, receiver in enumerate(receivers):
        keyboard.append([InlineKeyboardButton(f"مسح المستلم {i+1}: {receiver}", callback_data=f"remove_receiver:{i}")])
    
    keyboard.append([InlineKeyboardButton("الرجوع", callback_data="set_receivers")])
    return InlineKeyboardMarkup(keyboard)

def sending_info_menu(session, email_statuses):
    """قائمة معلومات الإرسال مع التحديث المستمر"""
    algorithm = session.get('sending_algorithm', 'parallel')
    algorithm_name = "متوازي (5 في نفس الوقت)" if algorithm == "parallel" else "مدرج (واحد تلو الآخر)"
    
    info_text = f"معلومات الإرسال الحالية:\n\n"
    info_text += f"الخوارزمية: {algorithm_name}\n\n"
    
    # معلومات الحسابات
    info_text += "الحسابات:\n"
    for email, status in email_statuses.items():
        info_text += f"• {email}: {status['sent_count']} مرسلة - {status['status']}\n"
    
    # المعلومات الأخرى
    info_text += f"\nالمواضيع: {len(session.get('subjects', []))}\n"
    info_text += f"الرسائل: {len(session.get('bodies', []))}\n"
    info_text += f"المستلمون: {len(session.get('receivers', []))}\n"
    info_text += f"العدد المطلوب: {session.get('count', 1)}\n"
    info_text += f"التأخير: {session.get('delay', 1)} ثانية"
    
    keyboard = [
        [InlineKeyboardButton("تحديث", callback_data="refresh_info")],
        [InlineKeyboardButton("إيقاف الإرسال", callback_data="stop_sending")],
        [InlineKeyboardButton("الرجوع", callback_data="back_to_menu")]
    ]
    
    return info_text, InlineKeyboardMarkup(keyboard)

def info_menu(session):
    bodies_text = ""
    for i, body in enumerate(session.get('bodies', [])):
        body_preview = re.sub(r'http\S+', '', body)
        bodies_text += f"الرسالة {i+1}: {body_preview[:50]}...\n" if len(body_preview) > 50 else f"الرسالة {i+1}: {body_preview}\n"
    
    subjects_text = ""
    for i, subject in enumerate(session.get('subjects', [])):
        subjects_text += f"الموضوع {i+1}: {subject}\n"
    
    receivers_text = ""
    for i, receiver in enumerate(session.get('receivers', [])):
        receivers_text += f"المستلم {i+1}: {receiver}\n"
    
    timing_text = ""
    for i, sender in enumerate(session.get('senders', [])):
        email_only = sender['email'].split(':')[0] if ':' in sender['email'] else sender['email']
        individual_delay = sender.get('individual_delay', 'إفتراضي')
        timing_text += f"{email_only}: {individual_delay}s\n"
    
    algorithm = session.get('sending_algorithm', 'parallel')
    algorithm_name = "متوازي (5 في نفس الوقت)" if algorithm == "parallel" else "مدرج (واحد تلو الآخر)"
    
    return (
        f"المعلومات المضافة:\n\n"
        f"خوارزمية الإرسال: {algorithm_name}\n\n"
        f"المستلمون:\n{receivers_text if receivers_text else 'غير محددين'}\n"
        f"المواضيع:\n{subjects_text if subjects_text else 'غير محددة'}\n"
        f"الرسائل:\n{bodies_text if bodies_text else 'غير محددة'}\n"
        f"التوقيتات:\n{timing_text if timing_text else 'جميعها بالإفتراضي'}\n"
        f"العدد: {session.get('count', 'غير محدد')}\n"
        f"التأخير الافتراضي: {session.get('delay', 'غير محدد')} ثانية\n"
        f"عدد الحسابات: {len(session.get('senders', []))}"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # التحقق مما إذا كانت هناك عملية إرسال نشطة
    if user_id in sending_in_progress and sending_in_progress[user_id]:
        await update.message.reply_text(
            "هناك عملية ارسال جارية حاليا!\n\n"
            "ارجو ايقاف العملية الحالية اولا باستخدام الامر:\n"
            "/stop\n\n"
            "او الانتظار حتى انتهاء العملية الحالية.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ايقاف الارسال الحالي", callback_data="stop_sending")],
                [InlineKeyboardButton("العودة للقائمة", callback_data="back_to_menu")]
            ])
        )
        return
    
    # حذف الرسائل السابقة عند الضغط على /start فقط
    await delete_previous_messages(chat_id, context)
    
    # التحقق من صلاحية المستخدم
    if is_allowed_user(user_id):
        # رسالة الترحيب للمستخدم المسموح له
        welcome_text = 'Welcome to the mail bot\n<a href="https://g.top4top.io/p_3578qa4ug1.jpg">&#8203;</a>'
        await send_new_message(
            context, chat_id, welcome_text,
            reply_markup=main_menu(user_id),
            parse_mode=ParseMode.HTML
        )
        
        # تهيئة الجلسة إذا لم تكن موجودة
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                'senders': [],
                'receivers': [],
                'subjects': [],
                'bodies': [],
                'count': 1,
                'delay': 1,
                'sending_algorithm': 'parallel'  # افتراضي: الإرسال المتوازي
            }
    else:
        # رسالة الترحيب للمستخدم غير المسموح له
        welcome_text = 'Welcome but you do not have permission to use the bot \n<a href="https://g.top4top.io/p_3578qa4ug1.jpg">&#8203;</a>'
        await send_new_message(
            context, chat_id, welcome_text,
            reply_markup=developer_contact_menu(),
            parse_mode=ParseMode.HTML
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    # التحقق من صلاحية المستخدم
    if not is_allowed_user(user_id):
        if data == "back_to_menu":
            # إعادة محاولة التحقق من الصلاحية
            if is_allowed_user(user_id):
                await query.edit_message_text(
                    "مرحبا بك في البوت الرئيسي",
                    reply_markup=main_menu(user_id)
                )
            else:
                welcome_text = 'Welcome but you do not have permission to use the bot \n<a href="https://g.top4top.io/p_3578qa4ug1.jpg">&#8203;</a>'
                await query.edit_message_text(
                    welcome_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=developer_contact_menu()
                )
        return
    
    if data == "back_to_menu":
        user_states[user_id] = None
        await query.edit_message_text(
            "مرحبا بك في البوت الرئيسي",
            reply_markup=main_menu(user_id)
        )
    
    elif data == "sending_algorithms":
        await query.edit_message_text(
            "اختر خوارزمية الارسال المفضلة:",
            reply_markup=sending_algorithms_menu()
        )
    
    elif data == "algorithm_parallel":
        user_sessions[user_id]['sending_algorithm'] = 'parallel'
        await query.edit_message_text(
            "تم تعيين خوارزمية الارسال الى: الارسال المتوازي\n\n"
            "سيتم ارسال 5 رسائل في نفس الوقت من حسابات مختلفة",
            reply_markup=main_menu(user_id)
        )
    
    elif data == "algorithm_sequential":
        user_sessions[user_id]['sending_algorithm'] = 'sequential'
        await query.edit_message_text(
            "تم تعيين خوارزمية الارسال الى: الارسال المدرج\n\n"
            "سيتم ارسال رسائل واحدة تلو الاخرى بشكل متسلسل",
            reply_markup=main_menu(user_id)
        )
    
    elif data == "refresh_info":
        # تحديث معلومات الإرسال
        session = user_sessions.get(user_id, {})
        email_statuses = sending_status.get(user_id, {})
        
        info_text, reply_markup = sending_info_menu(session, email_statuses)
        await query.edit_message_text(
            info_text,
            reply_markup=reply_markup
        )
    
    elif data == "stop_sending":
        stop_flags[user_id] = True
        # إلغاء المهمة النشطة إذا كانت موجودة
        if user_id in sending_tasks:
            sending_tasks[user_id].cancel()
            del sending_tasks[user_id]
        
        # تحديث حالة الإرسال
        sending_in_progress[user_id] = False
        
        await query.edit_message_text(
            "تم ايقاف الارسال بنجاح",
            reply_markup=main_menu(user_id)
        )
    
    elif data == "sending_info":
        session = user_sessions.get(user_id, {})
        email_statuses = sending_status.get(user_id, {})
        
        info_text, reply_markup = sending_info_menu(session, email_statuses)
        await query.edit_message_text(
            info_text,
            reply_markup=reply_markup
        )
    
    elif data == "manage_senders":
        await query.edit_message_text(
            "اختر خيار ادارة الحسابات:",
            reply_markup=senders_management_menu()
        )
    
    elif data == "add_senders":
        user_states[user_id] = "waiting_for_senders"
        await query.edit_message_text(
            "ارسل الحسابات على الشكل: email:password (كل حساب في سطر)",
            reply_markup=back_button()
        )
    
    elif data == "remove_senders":
        session = user_sessions.get(user_id, {})
        senders = session.get('senders', [])
        
        if not senders:
            await query.edit_message_text(
                "لا توجد حسابات مضافة للمسح",
                reply_markup=senders_management_menu()
            )
        else:
            await query.edit_message_text(
                "اختر الحساب الذي تريد ازالته:",
                reply_markup=remove_senders_menu(senders)
            )
    
    elif data.startswith("remove_sender:"):
        email_to_remove = data.split(":", 1)[1]
        session = user_sessions.get(user_id, {})
        senders = session.get('senders', [])
        
        new_senders = [s for s in senders if not s['email'].startswith(email_to_remove)]
        user_sessions[user_id]['senders'] = new_senders
        
        await query.edit_message_text(
            f"تم ازالة الحساب: {email_to_remove}",
            reply_markup=senders_management_menu()
        )
    
    elif data == "set_individual_timing":
        session = user_sessions.get(user_id, {})
        senders = session.get('senders', [])
        
        if not senders:
            await query.edit_message_text(
                "لا توجد حسابات مضافة",
                reply_markup=senders_management_menu()
            )
        else:
            await query.edit_message_text(
                "اختر الحساب لتعيين التوقيت الفردي:",
                reply_markup=individual_timing_menu(senders)
            )
    
    elif data.startswith("set_sender_delay:"):
        email_to_set = data.split(":", 1)[1]
        user_states[user_id] = f"waiting_sender_delay:{email_to_set}"
        await query.edit_message_text(
            f"ارسل وقت التأخير للحساب {email_to_set} (بالثواني):",
            reply_markup=back_button()
        )
    
    elif data == "set_receivers":
        await query.edit_message_text(
            "اختر خيار ادارة المستلمين:",
            reply_markup=receivers_management_menu()
        )
    
    elif data == "add_receiver":
        user_states[user_id] = "waiting_for_receiver"
        await query.edit_message_text(
            "ارسل البريد الالكتروني للمستلم:",
            reply_markup=back_button()
        )
    
    elif data == "remove_receiver":
        session = user_sessions.get(user_id, {})
        receivers = session.get('receivers', [])
        
        if not receivers:
            await query.edit_message_text(
                "لا توجد مستلمين مضافة للمسح",
                reply_markup=receivers_management_menu()
            )
        else:
            await query.edit_message_text(
                "اختر المستلم الذي تريد ازالته:",
                reply_markup=remove_receivers_menu(receivers)
            )
    
    elif data.startswith("remove_receiver:"):
        index = int(data.split(":", 1)[1])
        session = user_sessions.get(user_id, {})
        receivers = session.get('receivers', [])
        
        if 0 <= index < len(receivers):
            removed_receiver = receivers.pop(index)
            user_sessions[user_id]['receivers'] = receivers
            
            await query.edit_message_text(
                f"تم ازالة المستلم: {removed_receiver}",
                reply_markup=receivers_management_menu()
            )
        else:
            await query.edit_message_text(
                "خطأ في ازالة المستلم",
                reply_markup=receivers_management_menu()
            )
    
    elif data == "set_subjects":
        await query.edit_message_text(
            "اختر خيار ادارة المواضيع:",
            reply_markup=subjects_management_menu()
        )
    
    elif data == "add_subject":
        user_states[user_id] = "waiting_for_subject"
        await query.edit_message_text(
            "ارسل موضوع الرسالة:",
            reply_markup=back_button()
        )
    
    elif data == "remove_subject":
        session = user_sessions.get(user_id, {})
        subjects = session.get('subjects', [])
        
        if not subjects:
            await query.edit_message_text(
                "لا توجد مواضيع مضافة للمسح",
                reply_markup=subjects_management_menu()
            )
        else:
            await query.edit_message_text(
                "اختر الموضوع الذي تريد ازالته:",
                reply_markup=remove_subjects_menu(subjects)
            )
    
    elif data.startswith("remove_subject:"):
        index = int(data.split(":", 1)[1])
        session = user_sessions.get(user_id, {})
        subjects = session.get('subjects', [])
        
        if 0 <= index < len(subjects):
            removed_subject = subjects.pop(index)
            user_sessions[user_id]['subjects'] = subjects
            
            await query.edit_message_text(
                f"تم ازالة الموضوع: {removed_subject}",
                reply_markup=subjects_management_menu()
            )
        else:
            await query.edit_message_text(
                "خطأ في ازالة الموضوع",
                reply_markup=subjects_management_menu()
            )
    
    elif data == "set_bodies":
        await query.edit_message_text(
            "اختر خيار ادارة الرسائل:",
            reply_markup=bodies_management_menu()
        )
    
    elif data == "add_body":
        user_states[user_id] = "waiting_for_body"
        await query.edit_message_text(
            "ارسل نص الرسالة:",
            reply_markup=back_button()
        )
    
    elif data == "remove_body":
        session = user_sessions.get(user_id, {})
        bodies = session.get('bodies', [])
        
        if not bodies:
            await query.edit_message_text(
                "لا توجد رسائل مضافة للمسح",
                reply_markup=bodies_management_menu()
            )
        else:
            await query.edit_message_text(
                "اختر الرسالة التي تريد ازالته:",
                reply_markup=remove_bodies_menu(bodies)
            )
    
    elif data.startswith("remove_body:"):
        index = int(data.split(":", 1)[1])
        session = user_sessions.get(user_id, {})
        bodies = session.get('bodies', [])
        
        if 0 <= index < len(bodies):
            removed_body = bodies.pop(index)
            user_sessions[user_id]['bodies'] = bodies
            
            await query.edit_message_text(
                f"تم ازالة الرسالة: {removed_body[:50]}...",
                reply_markup=bodies_management_menu()
            )
        else:
            await query.edit_message_text(
                "خطأ في ازالة الرسالة",
                reply_markup=bodies_management_menu()
            )
    
    elif data == "admin_menu":
        if not is_admin(user_id):
            await query.edit_message_text("ليس لديك صلاحية للوصول إلى هذه القائمة.")
            return
        await query.edit_message_text(
            "مرحبا بك في قائمة الادارة",
            reply_markup=admin_menu()
        )
    
    elif data == "set_delay":
        user_states[user_id] = "waiting_for_delay"
        await query.edit_message_text(
            "ارسل وقت التأخير بين كل ارسال (بالثواني):",
            reply_markup=back_button()
        )
    
    elif data == "set_count":
        user_states[user_id] = "waiting_for_count"
        await query.edit_message_text(
            "ارسل عدد المرات التي تريد ارسال الرسالة فيها:",
            reply_markup=back_button()
        )
    
    elif data == "show_info":
        session = user_sessions.get(user_id, {})
        await query.edit_message_text(
            info_menu(session),
            reply_markup=back_button()
        )
    
    elif data == "start_sending":
        # التحقق مما إذا كانت هناك عملية إرسال نشطة
        if user_id in sending_in_progress and sending_in_progress[user_id]:
            await query.edit_message_text(
                "هناك عملية ارسال جارية حاليا!\n\n"
                "ارجو ايقاف العملية الحالية اولا باستخدام الامر:\n"
                "/stop\n\n"
                "او الانتظار حتى انتهاء العملية الحالية.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ايقاف الارسال الحالي", callback_data="stop_sending")],
                    [InlineKeyboardButton("العودة للقائمة", callback_data="back_to_menu")]
                ])
            )
            return
        
        session = user_sessions.get(user_id, {})
        if not session.get('senders'):
            await query.edit_message_text(
                "يجب اضافة حسابات اولا",
                reply_markup=back_button()
            )
            return
        
        if not session.get('receivers'):
            await query.edit_message_text(
                "يجب تعيين المستلمين اولا",
                reply_markup=back_button()
            )
            return
        
        await query.edit_message_text(
            "جاري بدء الارسال...",
            reply_markup=back_button()
        )
        
        stop_flags[user_id] = False
        sending_in_progress[user_id] = True  # تعيين حالة الإرسال كنشطة
        
        # إنشاء مهمة إرسال جديدة
        task = asyncio.create_task(send_all_emails(context, user_id, query.message))
        sending_tasks[user_id] = task
    
    elif data == "add_admin" and is_admin(user_id):
        user_states[user_id] = "waiting_for_admin_id"
        await query.edit_message_text(
            "ارسل ايدي المستخدم ليكون ادمن:",
            reply_markup=back_button()
        )
    
    elif data == "remove_admin" and is_admin(user_id):
        user_states[user_id] = "waiting_for_admin_id_to_remove"
        await query.edit_message_text(
            "ارسل ايدي المستخدم لازالته من الادمن:",
            reply_markup=back_button()
        )
    
    elif data == "add_user" and is_admin(user_id):
        user_states[user_id] = "waiting_for_user_id"
        await query.edit_message_text(
            "ارسل ايدي المستخدم لاضافته:",
            reply_markup=back_button()
        )
    
    elif data == "remove_user" and is_admin(user_id):
        user_states[user_id] = "waiting_for_user_id_to_remove"
        await query.edit_message_text(
            "ارسل ايدي المستخدم لازالته:",
            reply_markup=back_button()
        )
    
    elif data == "list_admins" and is_admin(user_id):
        admin_list = "\n".join(admins) if admins else "لا يوجد ادمن"
        await query.edit_message_text(
            f"قائمة الادمن:\n{admin_list}",
            reply_markup=back_button()
        )
    
    elif data == "list_users" and is_admin(user_id):
        user_list = "\n".join(allowed_users) if allowed_users else "لا يوجد مستخدمين مسموح لهم"
        await query.edit_message_text(
            f"قائمة المستخدمين المسموح لهم:\n{user_list}",
            reply_markup=back_button()
        )
    
    elif data == "toggle_bot_mode" and is_admin(user_id):
        global bot_paid_mode
        bot_paid_mode = not bot_paid_mode
        await query.edit_message_text(
            f"تم تغيير وضع البوت الى: {'مدفوع' if bot_paid_mode else 'مجاني'}",
            reply_markup=admin_menu()
        )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text
    
    if not is_allowed_user(user_id):
        await send_new_message(
            context, chat_id,
            "ليس لديك صلاحية لاستخدام هذا البوت.",
            reply_markup=developer_contact_menu()
        )
        return
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            'senders': [],
            'receivers': [],
            'subjects': [],
            'bodies': [],
            'count': 1,
            'delay': 1,
            'sending_algorithm': 'parallel'
        }
    
    state = user_states.get(user_id)
    
    if state == "waiting_for_senders":
        accounts = text.strip().split('\n')
        valid_accounts = []
        
        for account in accounts:
            if ':' in account:
                email, password = account.split(':', 1)
                if re.match(EMAIL_REGEX, email.strip()):
                    valid_accounts.append({'email': email.strip(), 'password': password.strip(), 'individual_delay': None})
        
        current_senders = user_sessions[user_id].get('senders', [])
        current_senders.extend(valid_accounts)
        user_sessions[user_id]['senders'] = current_senders
        
        user_states[user_id] = None
        await send_new_message(
            context, chat_id,
            f"تم اضافة {len(valid_accounts)} حساب بنجاح",
            reply_markup=main_menu(user_id)
        )
    
    elif state and state.startswith("waiting_sender_delay:"):
        email_to_set = state.split(":", 1)[1]
        try:
            delay = float(text)
            if delay < 0.1:
                delay = 0.1
            
            # تحديث التأخير الفردي للحساب
            session = user_sessions.get(user_id, {})
            for sender in session.get('senders', []):
                email_only = sender['email'].split(':')[0] if ':' in sender['email'] else sender['email']
                if email_only == email_to_set:
                    sender['individual_delay'] = delay
                    break
            
            user_states[user_id] = None
            await send_new_message(
                context, chat_id,
                f"تم تعيين التأخير للحساب {email_to_set} الى {delay} ثانية",
                reply_markup=senders_management_menu()
            )
        except ValueError:
            await send_new_message(
                context, chat_id,
                "ارجو ارسال رقم صحيح:",
                reply_markup=back_button()
            )
    
    elif state == "waiting_for_receiver":
        if re.match(EMAIL_REGEX, text.strip()):
            current_receivers = user_sessions[user_id].get('receivers', [])
            if len(current_receivers) < 3:
                current_receivers.append(text.strip())
                user_sessions[user_id]['receivers'] = current_receivers
                user_states[user_id] = None
                await send_new_message(
                    context, chat_id,
                    f"تم اضافة المستلم بنجاح ({len(current_receivers)}/3)",
                    reply_markup=receivers_management_menu()
                )
            else:
                await send_new_message(
                    context, chat_id,
                    "لقد وصلت للحد الاقصى للمستلمين (3 مستلمين)",
                    reply_markup=receivers_management_menu()
                )
        else:
            await send_new_message(
                context, chat_id,
                "البريد الالكتروني غير صحيح، ارجو ارسال بريد صحيح:",
                reply_markup=back_button()
            )
    
    elif state == "waiting_for_subject":
        current_subjects = user_sessions[user_id].get('subjects', [])
        if len(current_subjects) < 3:
            current_subjects.append(text)
            user_sessions[user_id]['subjects'] = current_subjects
            user_states[user_id] = None
            await send_new_message(
                context, chat_id,
                f"تم اضافة الموضوع بنجاح ({len(current_subjects)}/3)",
                reply_markup=subjects_management_menu()
            )
        else:
            await send_new_message(
                context, chat_id,
                "لقد وصلت للحد الاقصى للمواضيع (3 مواضيع)",
                reply_markup=subjects_management_menu()
            )
    
    elif state == "waiting_for_body":
        current_bodies = user_sessions[user_id].get('bodies', [])
        if len(current_bodies) < 3:
            current_bodies.append(text)
            user_sessions[user_id]['bodies'] = current_bodies
            user_states[user_id] = None
            await send_new_message(
                context, chat_id,
                f"تم اضافة الرسالة بنجاح ({len(current_bodies)}/3)",
                reply_markup=bodies_management_menu()
            )
        else:
            await send_new_message(
                context, chat_id,
                "لقد وصلت للحد الاقصى للرسائل (3 رسائل)",
                reply_markup=bodies_management_menu()
            )
    
    elif state == "waiting_for_delay":
        try:
            delay = float(text)
            if delay < 0.1:
                delay = 0.1
            user_sessions[user_id]['delay'] = delay
            user_states[user_id] = None
            await send_new_message(
                context, chat_id,
                f"تم تعيين التأخير الى {delay} ثانية",
                reply_markup=main_menu(user_id)
            )
        except ValueError:
            await send_new_message(
                context, chat_id,
                "ارجو ارسال رقم صحيح:",
                reply_markup=back_button()
            )
    
    elif state == "waiting_for_count":
        try:
            count = int(text)
            if count < 1:
                count = 1
            user_sessions[user_id]['count'] = count
            user_states[user_id] = None
            await send_new_message(
                context, chat_id,
                f"تم تعيين العدد الى {count}",
                reply_markup=main_menu(user_id)
            )
        except ValueError:
            await send_new_message(
                context, chat_id,
                "ارجو ارسال رقم صحيح:",
                reply_markup=back_button()
            )
    
    elif state == "waiting_for_admin_id" and is_admin(user_id):
        new_admin = text.strip()
        if new_admin not in admins:
            admins.append(new_admin)
            await send_new_message(
                context, chat_id,
                f"تم اضافة {new_admin} الى الادمن",
                reply_markup=admin_menu()
            )
        else:
            await send_new_message(
                context, chat_id,
                "هذا المستخدم موجود مسبقا في الادمن",
                reply_markup=admin_menu()
            )
        user_states[user_id] = None
    
    elif state == "waiting_for_admin_id_to_remove" and is_admin(user_id):
        admin_to_remove = text.strip()
        if admin_to_remove in admins and admin_to_remove != ADMIN_ID:
            admins.remove(admin_to_remove)
            await send_new_message(
                context, chat_id,
                f"تم ازالة {admin_to_remove} من الادمن",
                reply_markup=admin_menu()
            )
        else:
            await send_new_message(
                context, chat_id,
                "لا يمكن ازالة هذا الادمن",
                reply_markup=admin_menu()
            )
        user_states[user_id] = None
    
    elif state == "waiting_for_user_id" and is_admin(user_id):
        new_user = text.strip()
        if new_user not in allowed_users:
            allowed_users.append(new_user)
            await send_new_message(
                context, chat_id,
                f"تم اضافة {new_user} الى المستخدمين المسموح لهم",
                reply_markup=admin_menu()
            )
        else:
            await send_new_message(
                context, chat_id,
                "هذا المستخدم موجود مسبقا في القائمة",
                reply_markup=admin_menu()
            )
        user_states[user_id] = None
    
    elif state == "waiting_for_user_id_to_remove" and is_admin(user_id):
        user_to_remove = text.strip()
        if user_to_remove in allowed_users:
            allowed_users.remove(user_to_remove)
            await send_new_message(
                context, chat_id,
                f"تم ازالة {user_to_remove} من المستخدمين المسموح لهم",
                reply_markup=admin_menu()
            )
        else:
            await send_new_message(
                context, chat_id,
                "هذا المستخدم غير موجود في القائمة",
                reply_markup=admin_menu()
            )
        user_states[user_id] = None
    
    else:
        await send_new_message(
            context, chat_id,
            "اختر من القائمة:",
            reply_markup=main_menu(user_id)
        )

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    stop_flags[user_id] = True
    
    # إلغاء المهمة النشطة إذا كانت موجودة
    if user_id in sending_tasks:
        sending_tasks[user_id].cancel()
        del sending_tasks[user_id]
    
    # تحديث حالة الإرسال
    sending_in_progress[user_id] = False
    
    await send_new_message(
        context, chat_id,
        "تم ايقاف الارسال لهذا المستخدم.",
        reply_markup=main_menu(user_id)
    )

def format_status_report(email_statuses):
    """تنسيق تقرير الحالة"""
    report_lines = []
    for email, status_info in email_statuses.items():
        line = f"{email} • {status_info['status']} • {status_info['sent_count']} رسائل"
        report_lines.append(line)
    
    report = "\n".join(report_lines)
    return f"```\n{report}\n```"

async def send_email_with_retry(sender_email, sender_password, receiver, subject, body, max_retries=2):
    """إرسال بريد إلكتروني مع إعادة المحاولة - غير متزامن"""
    for attempt in range(max_retries):
        try:
            # إنشاء رسالة
            message = MIMEMultipart()
            message['From'] = sender_email
            message['To'] = receiver
            message['Subject'] = Header(subject, 'utf-8')
            message.attach(MIMEText(body, 'plain', 'utf-8'))
            
            # إرسال الرسالة
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, receiver, message.as_string())
            server.quit()
            
            return True, None  # نجح
            
        except smtplib.SMTPAuthenticationError as e:
            return False, "محظور"  # فشل في المصادقة
            
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(2)  # انتظار غير متزامن قبل إعادة المحاولة
            else:
                return False, f"خطأ: {str(e)[:50]}"
    
    return False, "فشل بعد إعادة المحاولة"

async def send_all_emails(context: ContextTypes.DEFAULT_TYPE, user_id: int, msg):
    session = user_sessions.get(user_id)
    if not session:
        await context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            text="لم يتم العثور على جلسة المستخدم."
        )
        sending_in_progress[user_id] = False
        return
    
    senders = session.get('senders', [])
    receivers = session.get('receivers', [])
    subjects = session.get('subjects', [])
    bodies = session.get('bodies', [])
    count = session.get('count', 1)
    global_delay = session.get('delay', 1)
    algorithm = session.get('sending_algorithm', 'parallel')
    
    if not senders or not receivers:
        await context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            text="يجب تعيين الحسابات والمستلمين اولا."
        )
        sending_in_progress[user_id] = False
        return
    
    # استخدام القيم الافتراضية إذا لم يتم تعيينها
    if not subjects:
        subjects = ["لا يوجد موضوع"]
    if not bodies:
        bodies = ["لا يوجد محتوى"]
    
    # تهيئة حالة كل إيميل
    email_statuses = {}
    for sender in senders:
        email = sender['email'].split(':')[0] if ':' in sender['email'] else sender['email']
        email_statuses[email] = {
            'status': 'جاري الارسال',
            'sent_count': 0,
            'last_error': '',
            'active': True,
            'error_count': 0
        }
    
    total_sent = 0
    stop_flags[user_id] = False
    sending_status[user_id] = email_statuses
    
    # إنشاء رسالة التقرير الأولى
    algorithm_name = "متوازي (5 في نفس الوقت)" if algorithm == "parallel" else "مدرج (واحد تلو الآخر)"
    status_report = format_status_report(email_statuses)
    progress_text = f"بدء عملية الارسال...\n\nالخوارزمية: {algorithm_name}\n\n{status_report}\n\nللايقاف ارسل /stop"
    progress_msg = await context.bot.send_message(
        chat_id=msg.chat_id,
        text=progress_text,
        parse_mode=ParseMode.MARKDOWN
    )
    
    try:
        active_senders = [s for s in senders if email_statuses[s['email'].split(':')[0] if ':' in s['email'] else s['email']]['active']]
        
        if algorithm == "parallel":
            # الخوارزمية المتوازية - الإرسال ب5 في نفس الوقت
            total_sent = await send_parallel_emails(context, user_id, msg, progress_msg, active_senders, receivers, subjects, bodies, count, global_delay, email_statuses)
        else:
            # الخوارزمية المتسلسلة - الإرسال واحد تلو الآخر
            total_sent = await send_sequential_emails(context, user_id, msg, progress_msg, active_senders, receivers, subjects, bodies, count, global_delay, email_statuses)
        
        # بعد الانتهاء من الإرسال
        active_emails = len([s for s in email_statuses.values() if s['active']])
        total_emails_used = len([s for s in email_statuses.values() if s['sent_count'] > 0])
        
        if stop_flags.get(user_id):
            final_text = "تم ايقاف الارسال بواسطة المستخدم"
        else:
            final_text = (
                "انتهت عملية الارسال\n\n"
                f"اجمالي الرسائل: {total_sent}\n"
                f"الايميلات المستخدمة: {total_emails_used}\n"
                f"الايميلات النشطة: {active_emails}\n\n"
                "للبداية ارسل /start"
            )
        
        await context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=progress_msg.message_id,
            text=final_text
        )
        
        # تنظيف المهمة
        if user_id in sending_tasks:
            del sending_tasks[user_id]
        
        # تحديث حالة الإرسال
        sending_in_progress[user_id] = False
        
    except asyncio.CancelledError:
        # تم إلغاء المهمة
        await context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=progress_msg.message_id,
            text="تم الغاء عملية الارسال"
        )
        sending_in_progress[user_id] = False
    except Exception as e:
        print(f"Error in sending process: {e}")
        await context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=progress_msg.message_id,
            text=f"حدث خطأ في عملية الارسال: {str(e)}"
        )
        
        # تنظيف المهمة في حالة الخطأ
        if user_id in sending_tasks:
            del sending_tasks[user_id]
        sending_in_progress[user_id] = False

async def send_parallel_emails(context, user_id, msg, progress_msg, active_senders, receivers, subjects, bodies, count, global_delay, email_statuses):
    """الإرسال المتوازي - 5 إيميلات في نفس الوقت"""
    total_sent = 0
    
    for i in range(count):
        if stop_flags.get(user_id) or not active_senders:
            break
        
        # تحديث القائمة النشطة
        active_senders = [s for s in active_senders if email_statuses[s['email'].split(':')[0] if ':' in s['email'] else s['email']]['active']]
        
        if not active_senders:
            break
        
        # إرسال 5 إيميلات في نفس الوقت
        batch_size = min(5, len(active_senders))
        tasks = []
        
        for j in range(batch_size):
            if j >= len(active_senders):
                break
                
            sender = active_senders[j]
            email_key = sender['email'].split(':')[0] if ':' in sender['email'] else sender['email']
            
            # اختيار عشوائي للموضوع والرسالة والمستلم
            import random
            subject = random.choice(subjects)
            body_text = random.choice(bodies)
            receiver_email = random.choice(receivers)
            
            # إنشاء مهمة الإرسال
            task = send_email_with_retry(
                sender['email'], 
                sender['password'], 
                receiver_email, 
                subject, 
                body_text,
                MAX_RETRIES
            )
            tasks.append((email_key, sender, task))
        
        # تنفيذ جميع مهام الإرسال في نفس الوقت
        for email_key, sender, task in tasks:
            if stop_flags.get(user_id):
                break
            
            success, error = await task
            
            if success:
                total_sent += 1
                email_statuses[email_key]['sent_count'] += 1
                email_statuses[email_key]['status'] = 'شغال'
                email_statuses[email_key]['last_error'] = ''
                email_statuses[email_key]['error_count'] = 0
            else:
                email_statuses[email_key]['error_count'] += 1
                
                if "محظور" in str(error) or email_statuses[email_key]['error_count'] >= 2:
                    email_statuses[email_key]['status'] = 'محظور' if "محظور" in str(error) else 'متوقف'
                    email_statuses[email_key]['active'] = False
                    email_statuses[email_key]['last_error'] = error
                else:
                    email_statuses[email_key]['status'] = 'مشكلة مؤقتة'
                    email_statuses[email_key]['last_error'] = error
        
        # تحديث التقرير بعد كل باتش
        status_report = format_status_report(email_statuses)
        progress_text = f"حالة الارسال - الاجمالي: {total_sent}\n\nالخوارزمية: متوازي\n\n{status_report}\n\nللايقاف ارسل /stop"
        
        await context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=progress_msg.message_id,
            text=progress_text,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # الانتظار قبل الباتش التالي
        if not stop_flags.get(user_id) and i < count - 1 and active_senders:
            individual_delay = active_senders[0].get('individual_delay')
            wait_time = individual_delay if individual_delay is not None else global_delay
            await asyncio.sleep(wait_time)
    
    return total_sent

async def send_sequential_emails(context, user_id, msg, progress_msg, active_senders, receivers, subjects, bodies, count, global_delay, email_statuses):
    """الإرسال المتسلسل - واحد تلو الآخر"""
    total_sent = 0
    current_sender_index = 0
    
    for i in range(count * len(active_senders)):
        if stop_flags.get(user_id) or not active_senders:
            break
        
        # تحديث القائمة النشطة
        active_senders = [s for s in active_senders if email_statuses[s['email'].split(':')[0] if ':' in s['email'] else s['email']]['active']]
        
        if not active_senders:
            break
        
        # اختيار المرسل الحالي (بشكل دوري)
        current_sender = active_senders[current_sender_index % len(active_senders)]
        email_key = current_sender['email'].split(':')[0] if ':' in current_sender['email'] else current_sender['email']
        
        # اختيار عشوائي للموضوع والرسالة والمستلم
        import random
        subject = random.choice(subjects)
        body_text = random.choice(bodies)
        receiver_email = random.choice(receivers)
        
        # إرسال الرسالة
        success, error = await send_email_with_retry(
            current_sender['email'], 
            current_sender['password'], 
            receiver_email, 
            subject, 
            body_text,
            MAX_RETRIES
        )
        
        if success:
            total_sent += 1
            email_statuses[email_key]['sent_count'] += 1
            email_statuses[email_key]['status'] = 'شغال'
            email_statuses[email_key]['last_error'] = ''
            email_statuses[email_key]['error_count'] = 0
        else:
            email_statuses[email_key]['error_count'] += 1
            
            if "محظور" in str(error) or email_statuses[email_key]['error_count'] >= 2:
                email_statuses[email_key]['status'] = 'محظور' if "محظور" in str(error) else 'متوقف'
                email_statuses[email_key]['active'] = False
                email_statuses[email_key]['last_error'] = error
                # إزالة المرسل من القائمة النشطة
                if current_sender in active_senders:
                    active_senders.remove(current_sender)
            else:
                email_statuses[email_key]['status'] = 'مشكلة مؤقتة'
                email_statuses[email_key]['last_error'] = error
        
        # الانتقال للمرسل التالي
        current_sender_index = (current_sender_index + 1) % len(active_senders)
        
        # تحديث التقرير بعد كل إرسال
        status_report = format_status_report(email_statuses)
        progress_text = f"حالة الارسال - الاجمالي: {total_sent}\n\nالخوارزمية: مدرج\n\n{status_report}\n\nللايقاف ارسل /stop"
        
        await context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=progress_msg.message_id,
            text=progress_text,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # الانتظار قبل الإرسال التالي
        if not stop_flags.get(user_id) and i < (count * len(active_senders)) - 1:
            individual_delay = current_sender.get('individual_delay')
            wait_time = individual_delay if individual_delay is not None else global_delay
            await asyncio.sleep(wait_time)
    
    return total_sent

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    print("Bot is running with improved message management...")
    app.run_polling()

if __name__ == "__main__":
    main()
