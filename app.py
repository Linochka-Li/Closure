import os
import pandas as pd
import numpy as np
from flask import Flask, request, render_template, jsonify, send_file
from werkzeug.utils import secure_filename
import io
import re

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'xlsx', 'xls'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def find_price_column(df):
    """Ищет столбец с ценой (CPM) по названию."""
    price_keywords = ['цена', 'cpm', 'ecpm', 'price', 'ставка', 'cost']
    for col in df.columns:
        col_lower = col.lower().strip()
        if any(kw in col_lower for kw in price_keywords):
            return col
    return None

def detect_dsp_start(df, price_col):
    """
    Определяет индекс (в исходном DataFrame), с которого начинается ДСП.
    Критерий: первая строка, где цена (CPM) имеет дробную часть.
    Если таких нет, возвращает None (тогда будем использовать запасной метод).
    """
    for idx, row in df.iterrows():
        val = row[price_col]
        # Проверяем, является ли число целым (с учётом погрешности)
        if not pd.isna(val) and isinstance(val, (int, float)):
            if abs(val - round(val)) > 1e-9:  # есть дробная часть
                return idx
    return None

def detect_dsp_by_sum_jump(df_sorted):
    """Запасной метод: ищем скачок сумм >10 раз."""
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

    # 2. Очистка: удаляем нулевые строки
    df = df[df['Показы'] > 0]
    df = df[df['Сумма без НДС'] > 0]
    df = df[~df['Кампания'].astype(str).str.contains('olv_campaign', case=False, na=False)]
    df = df.dropna(subset=['Кампания'])

    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 3. Определяем столбец с ценой
    price_col = find_price_column(df)
    dsp_start_idx = None

    if price_col is not None:
        # Пытаемся найти первую дробную цену в исходном порядке
        dsp_start_idx = detect_dsp_start(df, price_col)

    # 4. Если не нашли дробную цену, используем запасной метод (скачок сумм)
    if dsp_start_idx is None:
        # Сортируем по убыванию суммы
        df_sorted = df.sort_values(by='Сумма без НДС', ascending=False).reset_index(drop=True)
        jump_idx = detect_dsp_by_sum_jump(df_sorted)
        if jump_idx is not None:
            # Помечаем в исходном df по индексам из df_sorted
            # Чтобы не усложнять, просто создадим копию и добавим тип позже
            # Но проще: после сортировки разметить и потом группировать
            df_sorted.loc[:jump_idx-1, 'Тип'] = 'Основной'
            df_sorted.loc[jump_idx:, 'Тип'] = 'ДСП'
            df = df_sorted
        else:
            # Если ничего не нашли, всё основное
            df['Тип'] = 'Основной'
    else:
        # Размечаем по найденному индексу (в исходном порядке)
        # Создаём копию, чтобы не менять исходный порядок при группировке
        df = df.copy()
        # Все строки до dsp_start_idx (исключая) - основные, начиная с него - ДСП
        # Используем .iloc для позиционной индексации
        # Но у нас индекс может быть не числовым, поэтому сбросим индекс
        df_reset = df.reset_index(drop=True)
        # Найдём позицию (номер строки) для dsp_start_idx
        # Так как df_reset.index - это позиция, а dsp_start_idx - это исходный индекс (может быть не по порядку)
        # Мы должны найти позицию строки с этим индексом в df_reset
        # Проще: пройти по df_reset и найти, где индекс совпадает
        pos = None
        for i, row in df_reset.iterrows():
            if row.name == dsp_start_idx:  # row.name - это исходный индекс
                pos = i
                break
        if pos is not None:
            df_reset.loc[:pos-1, 'Тип'] = 'Основной'
            df_reset.loc[pos:, 'Тип'] = 'ДСП'
            df = df_reset
        else:
            # fallback: если не нашли, всё основное
            df['Тип'] = 'Основной'

    # 5. Группировка
    agg_funcs = {
        'Показы': lambda x: round(sum(x) / 1000, 2),
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
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
