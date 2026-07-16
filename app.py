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
    price_keywords = ['цена', 'cpm', 'ecpm', 'price', 'ставка', 'cost']
    for col in df.columns:
        col_lower = col.lower().strip()
        if any(kw in col_lower for kw in price_keywords):
            return col
    return None

def detect_dsp_start(df, price_col):
    for idx, row in df.iterrows():
        val = row[price_col]
        if not pd.isna(val) and isinstance(val, (int, float)):
            if abs(val - round(val)) > 1e-9:
                return idx
    return None

def detect_dsp_by_sum_jump(df_sorted):
    for i in range(1, len(df_sorted)):
        prev = df_sorted.iloc[i-1]['Сумма без НДС']
        curr = df_sorted.iloc[i]['Сумма без НДС']
        if prev / curr > 10:
            return i
    return None

def process_supplier_file(filepath):
    df = pd.read_excel(filepath, sheet_name=0)
    df.columns = df.columns.str.strip()

    # Очистка
    df = df[df['Показы'] > 0]
    df = df[df['Сумма без НДС'] > 0]
    # Удаляем olv_campaign (с пробелами и разным регистром)
    df['Кампания_clean'] = df['Кампания'].astype(str).str.strip().str.lower()
    df = df[~df['Кампания_clean'].str.contains('olv_campaign', na=False)]
    df = df.drop(columns=['Кампания_clean'])
    df = df.dropna(subset=['Кампания'])

    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    price_col = find_price_column(df)
    dsp_start_idx = None

    if price_col is not None:
        dsp_start_idx = detect_dsp_start(df, price_col)

    if dsp_start_idx is None:
        df_sorted = df.sort_values(by='Сумма без НДС', ascending=False).reset_index(drop=True)
        jump_idx = detect_dsp_by_sum_jump(df_sorted)
        if jump_idx is not None:
            df_sorted.loc[:jump_idx-1, 'Тип'] = 'Основной'
            df_sorted.loc[jump_idx:, 'Тип'] = 'ДСП'
            df = df_sorted
        else:
            df['Тип'] = 'Основной'
    else:
        df = df.copy()
        df_reset = df.reset_index(drop=True)
        pos = None
        for i, row in df_reset.iterrows():
            if row.name == dsp_start_idx:
                pos = i
                break
        if pos is not None:
            df_reset.loc[:pos-1, 'Тип'] = 'Основной'
            df_reset.loc[pos:, 'Тип'] = 'ДСП'
            df = df_reset
        else:
            df['Тип'] = 'Основной'

    agg_funcs = {
        'Показы': lambda x: round(sum(x) / 1000, 3),   # три знака
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
