import os
import pandas as pd
import numpy as np
from flask import Flask, request, render_template, jsonify, send_file
from werkzeug.utils import secure_filename
import io

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'xlsx', 'xls'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def find_price_column(df):
    """Находит столбец с ценой (CPM)."""
    # Точное совпадение (без учёта регистра и пробелов)
    for col in df.columns:
        col_clean = col.strip().lower()
        if col_clean == 'цена':
            return col
    # По ключевым словам
    keywords = ['cpm', 'ecpm', 'price', 'ставка', 'cost']
    for col in df.columns:
        col_clean = col.strip().lower()
        if any(kw in col_clean for kw in keywords):
            return col
    return None

def detect_dsp_start(df, price_col):
    """
    Возвращает индекс (позицию) первой строки с дробной ценой.
    Если все цены целые — возвращает None.
    """
    for idx, row in df.iterrows():
        val = row[price_col]
        try:
            val = float(val)
        except (ValueError, TypeError):
            continue
        # Проверяем, есть ли дробная часть
        if abs(val - round(val)) > 1e-9:
            return idx
    return None

def detect_dsp_by_sum_jump(df_sorted):
    """
    Запасной метод: ищем скачок сумм >10 раз.
    """
    for i in range(1, len(df_sorted)):
        prev = df_sorted.iloc[i-1]['Сумма без НДС']
        curr = df_sorted.iloc[i]['Сумма без НДС']
        if prev / curr > 10:
            return i
    return None

def process_supplier_file(filepath):
    # 1. Читаем первый лист
    df = pd.read_excel(filepath, sheet_name=0)
    df.columns = df.columns.str.strip()

    # 2. Очистка:
    # - удаляем нулевые показы и суммы
    df = df[df['Показы'] > 0]
    df = df[df['Сумма без НДС'] > 0]

    # - удаляем строки с olv_camp (любое окончание)
    df['Кампания_clean'] = df['Кампания'].astype(str).str.strip().str.lower()
    df = df[~df['Кампания_clean'].str.contains('olv_camp', na=False)]
    df = df.drop(columns=['Кампания_clean'])

    # - удаляем пустые названия кампаний
    df = df.dropna(subset=['Кампания'])
    df = df[df['Кампания'].astype(str).str.strip() != '']

    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 3. Сбрасываем индекс, чтобы строки шли по порядку
    df = df.reset_index(drop=True)

    # 4. Находим столбец с ценой
    price_col = find_price_column(df)
    dsp_start_pos = None

    if price_col is not None:
        dsp_start_pos = detect_dsp_start(df, price_col)

    # 5. Если не нашли дробную цену — используем запасной метод
    if dsp_start_pos is None:
        # Сортируем по убыванию суммы
        df_sorted = df.sort_values(by='Сумма без НДС', ascending=False).reset_index(drop=True)
        jump_pos = detect_dsp_by_sum_jump(df_sorted)
        if jump_pos is not None:
            df_sorted.loc[:jump_pos-1, 'Тип'] = 'Основной'
            df_sorted.loc[jump_pos:, 'Тип'] = 'ДСП'
            df = df_sorted
        else:
            # Если ничего не нашли — всё основное
            df['Тип'] = 'Основной'
    else:
        # Размечаем: все строки до dsp_start_pos (не включая её) — Основное,
        # начиная с dsp_start_pos — ДСП
        df.loc[:dsp_start_pos-1, 'Тип'] = 'Основной'
        df.loc[dsp_start_pos:, 'Тип'] = 'ДСП'

    # 6. Группировка
    agg_funcs = {
        'Показы': lambda x: round(sum(x) / 1000, 3),   # три знака после запятой
        'Сумма без НДС': 'sum'
    }

    main_df = df[df['Тип'] == 'Основной'].groupby('Кампания').agg(agg_funcs).reset_index()
    main_df.columns = ['Кампания', 'Показы (тыс.)', 'Сумма без НДС']

    dsp_df = df[df['Тип'] == 'ДСП'].groupby('Кампания').agg(agg_funcs).reset_index()
    dsp_df.columns = ['Кампания', 'Показы (тыс.)', 'Сумма без НДС']

    if not main_df.empty:
        main_df.loc['Итого'] = ['Итого', main_df['Показы (тыс.)'].sum(), main_df['Сумма без НДС'].sum()]
    if not dsp_df.empty:
        dsp_df.loc['Итого'] = ['Итого', dsp_df['Показы (тыс.)'].sum(), dsp_df['Сумма без НДС'].sum()]

    return main_df, dsp_df

# Flask routes (без изменений)
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'Файл не выбран'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Имя файла пустое'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'Разрешены только .xlsx и .xls'}), 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    try:
        main_df, dsp_df = process_supplier_file(filepath)
        main_json = main_df.to_dict(orient='records') if not main_df.empty else []
        dsp_json = dsp_df.to_dict(orient='records') if not dsp_df.empty else []
        app.config['LAST_MAIN'] = main_df
        app.config['LAST_DSP'] = dsp_df
        return jsonify({'main': main_json, 'dsp': dsp_json, 'filename': filename})
    except Exception as e:
        return jsonify({'error': f'Ошибка обработки: {str(e)}'}), 500
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)

@app.route('/download')
def download_result():
    main_df = app.config.get('LAST_MAIN')
    dsp_df = app.config.get('LAST_DSP')
    if main_df is None or dsp_df is None:
        return jsonify({'error': 'Нет обработанных данных'}), 400
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if not main_df.empty:
            main_df.to_excel(writer, sheet_name='Основное', index=False)
        if not dsp_df.empty:
            dsp_df.to_excel(writer, sheet_name='ДСП', index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='обработанная_сверка.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
