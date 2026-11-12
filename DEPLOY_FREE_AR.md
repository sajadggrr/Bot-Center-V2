# النشر المجاني — Bot Center V2

## الخطة المقترحة
- الواجهة: GitHub Pages الحالية.
- خادم FastAPI: Render Free Web Service.
- قاعدة البيانات: Neon PostgreSQL Free.
- لا تضع أي توكن أو كلمة مرور داخل GitHub.

## قبل إنشاء خدمة Render
1. أنشئ مشروع PostgreSQL مجاني في Neon.
2. انسخ رابط الاتصال **Pooled connection string** أو رابط الاتصال العادي، وتأكد أنه يبدأ بـ `postgresql://`.
3. في Render اختر New > Blueprint ثم اربط المستودع `sajadggrr/Bot-Center-V2`.
4. سيقرأ Render ملف `render.yaml`. أدخل القيم السرية المطلوبة عند الطلب:
   - `DATABASE_URL`: رابط Neon.
   - `BOT_TOKEN_ENCRYPTION_KEY`: مفتاح Fernet صالح.
   - `TELEGRAM_BOT_TOKEN`: توكن بوت المنصة. لا ترسله في المحادثة ولا تضعه في GitHub.
5. بعد النشر افتح رابط الخدمة وأضف `/health` للتحقق من ظهور `{"ok":true}`.

## إنشاء مفتاح تشفير Fernet
على جهاز فيه Python والحزمة cryptography:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
لا تستخدم قيمة عشوائية عادية؛ يجب أن يكون المفتاح ناتجاً عن Fernet.generate_key().

## حدود الخطة المجانية
Render Free يوقف الخدمة بعد 15 دقيقة من عدم وصول طلبات، وقد يستغرق إيقاظها قرابة دقيقة. كما أن ملفات الخدمة المحلية مؤقتة؛ لذلك لا نخزن قاعدة البيانات على قرص Render.
Neon Free يوفر PostgreSQL بسعة 1 GB للمشروع و100 CU-hour شهرياً حسب حدود الخطة الحالية. قد يتوقف الاتصال عند استنفاد الحصة حتى تجددها.
هذه الخطة للتجربة والبداية، وليست ضمان تشغيل دائم أو مناسباً لخدمة عامة حرجة. راجع حدود الخدمات قبل الإطلاق.

## بعد النشر
حدّث `static/config.js` إلى رابط API الذي يعطيه Render، ثم اختبر فتح Mini App من Telegram.
