import os
import pandas as pd
import numpy as np
from flask import Flask, request, render_template, jsonify, send_file
from werkzeug.utils import secure_filename
import io

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB
ALLOWED_EXTENSIONS = {'xlsx', 'xls'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def detect_dsp_start(df_sorted):
    """
    Определяет индекс (в отсортированном DataFrame), с которого начинается ДСП.
    Критерии:
      1) Первое дробное значение в столбце 'Сумма без НДС'
      2) Если дробных нет – ищем скачок >10 раз между соседними суммами
    """
    # Признак 1: дробное число
    for i, val in enumerate(df_sorted['Сумма без НДС']):
        if val % 1 != 0:
            return i

    # Признак 2: скачок
    for i in range(1, len(df_sorted)):
        prev = df_sorted.iloc[i-1]['Сумма без НДС']
        curr = df_sorted.iloc[i]['Сумма без НДС']
        if prev / curr > 10:   # можно подкрутить порог, если нужно
            return i

    # Если ничего не нашли – ДСП нет
    return None

def process_supplier_file(filepath):
    """
    Основная функция обработки файла подрядчика.
    Возвращает два DataFrame: основной и ДСП (сгруппированные по кампаниям)
    """
    # 1. Читаем первый лист (можно доработать поиск листа "ДСП", но пока так)
    df = pd.read_excel(filepath, sheet_name=0)

    # 2. Приводим названия столбцов к единому виду (убираем пробелы)
    df.columns = df.columns.str.strip()

    # 3. Очистка: удаляем строки с нулевыми показами или суммой
    df = df[df['Показы'] > 0]
    df = df[df['Сумма без НДС'] > 0]

    # 4. Удаляем olv_campaign (регистронезависимо)
    df = df[~df['Кампания'].astype(str).str.contains('olv_campaign', case=False, na=False)]

    # 5. Удаляем строки, где Кампания пустая или NaN
    df = df.dropna(subset=['Кампания'])

    # Если после очистки ничего не осталось
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 6. Сортируем по убыванию суммы
    df_sorted = df.sort_values(by='Сумма без НДС', ascending=False).reset_index(drop=True)

    # 7. Определяем точку ДСП
    dsp_index = detect_dsp_start(df_sorted)

    # 8. Размечаем тип
    if dsp_index is not None:
        df_sorted.loc[:dsp_index-1, 'Тип'] = 'Основной'
        df_sorted.loc[dsp_index:, 'Тип'] = 'ДСП'
    else:
        df_sorted['Тип'] = 'Основной'

    # 9. Группировка
    agg_funcs = {
        'Показы': lambda x: round(sum(x) / 1000, 2),  # в тысячах
        'Сумма без НДС': 'sum'
    }

    main_df = df_sorted[df_sorted['Тип'] == 'Основной'].groupby('Кампания').agg(agg_funcs).reset_index()
    main_df.columns = ['Кампания', 'Показы (тыс.)', 'Сумма без НДС']

    dsp_df = df_sorted[df_sorted['Тип'] == 'ДСП'].groupby('Кампания').agg(agg_funcs).reset_index()
    dsp_df.columns = ['Кампания', 'Показы (тыс.)', 'Сумма без НДС']

    # Добавляем итоговую строку (опционально)
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
        # Преобразуем в JSON для отправки на фронт
        main_json = main_df.to_dict(orient='records') if not main_df.empty else []
        dsp_json = dsp_df.to_dict(orient='records') if not dsp_df.empty else []

        # Сохраняем обработанные данные в сессии (или во временный файл) для скачивания
        # Просто сохраним как глобальную переменную (для простоты, но в проде лучше использовать сессию)
        app.config['LAST_MAIN'] = main_df
        app.config['LAST_DSP'] = dsp_df

        return jsonify({
            'main': main_json,
            'dsp': dsp_json,
            'filename': filename
        })
    except Exception as e:
        return jsonify({'error': f'Ошибка обработки: {str(e)}'}), 500
    finally:
        # Удаляем загруженный файл, чтобы не захламлять
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
    app.run(debug=True, host='0.0.0.0', port=5000)