<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Обработчик сверок ДСП</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #f5f7fa; padding-top: 2rem; }
        .card { border-radius: 1rem; box-shadow: 0 10px 25px rgba(0,0,0,0.05); }
        .table-responsive { max-height: 400px; overflow-y: auto; }
        .loader { display: none; }
        td, th { text-align: right; }
        td:first-child, th:first-child { text-align: left; }
    </style>
</head>
<body>
<div class="container">
    <h1 class="mb-4 text-center">📊 Обработка сверок подрядчиков</h1>
    <div class="row justify-content-center">
        <div class="col-lg-8">
            <div class="card p-4">
                <form id="uploadForm" enctype="multipart/form-data">
                    <div class="mb-3">
                        <label for="fileInput" class="form-label">Загрузите файл сверки (.xlsx)</label>
                        <input class="form-control" type="file" id="fileInput" name="file" accept=".xlsx,.xls" required>
                    </div>
                    <button type="submit" class="btn btn-primary w-100" id="processBtn">
                        <span id="btnText">🔍 Обработать</span>
                        <span class="loader spinner-border spinner-border-sm" id="loader"></span>
                    </button>
                </form>
            </div>
        </div>
    </div>

    <div id="results" style="display: none;" class="mt-4">
        <ul class="nav nav-tabs" id="resultTabs" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link active" id="main-tab" data-bs-toggle="tab" data-bs-target="#main" type="button" role="tab">Основное</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="dsp-tab" data-bs-toggle="tab" data-bs-target="#dsp" type="button" role="tab">ДСП</button>
            </li>
        </ul>
        <div class="tab-content p-3 border border-top-0 rounded-bottom bg-white">
            <div class="tab-pane fade show active" id="main" role="tabpanel">
                <div class="table-responsive" id="mainTableWrap"></div>
            </div>
            <div class="tab-pane fade" id="dsp" role="tabpanel">
                <div class="table-responsive" id="dspTableWrap"></div>
            </div>
        </div>
        <div class="d-flex justify-content-end mt-3">
            <button id="downloadBtn" class="btn btn-success">⬇️ Скачать результат (Excel)</button>
        </div>
    </div>

    <div id="errorAlert" class="alert alert-danger mt-3" style="display: none;"></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
    const form = document.getElementById('uploadForm');
    const resultsDiv = document.getElementById('results');
    const errorAlert = document.getElementById('errorAlert');
    const mainTableWrap = document.getElementById('mainTableWrap');
    const dspTableWrap = document.getElementById('dspTableWrap');
    const loader = document.getElementById('loader');
    const btnText = document.getElementById('btnText');

    function renderTable(data, container) {
        if (!data || data.length === 0) {
            container.innerHTML = '<p class="text-muted">Нет данных</p>';
            return;
        }
        let html = '<table class="table table-striped table-hover"><thead><tr>';
        const headers = Object.keys(data[0]);
        headers.forEach(h => html += `<th>${h}</th>`);
        html += '</tr></thead><tbody>';
        data.forEach(row => {
            html += '<tr>';
            headers.forEach(h => {
                let val = row[h] !== undefined ? row[h] : '';
                if (typeof val === 'number') {
                    // Если столбец содержит "Показы" — три знака, иначе два
                    if (h.includes('Показы') || h.includes('показы')) {
                        val = val.toFixed(3);
                    } else {
                        val = val.toFixed(2);
                    }
                }
                html += `<td>${val}</td>`;
            });
            html += '</tr>';
        });
        html += '</tbody></table>';
        container.innerHTML = html;
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        errorAlert.style.display = 'none';
        resultsDiv.style.display = 'none';
        loader.style.display = 'inline-block';
        btnText.textContent = 'Обработка...';

        const formData = new FormData(form);
        try {
            const response = await fetch('/upload', {
                method: 'POST',
                body: formData
            });
            const result = await response.json();
            if (response.ok) {
                renderTable(result.main, mainTableWrap);
                renderTable(result.dsp, dspTableWrap);
                resultsDiv.style.display = 'block';
            } else {
                errorAlert.textContent = result.error || 'Ошибка';
                errorAlert.style.display = 'block';
            }
        } catch (err) {
            errorAlert.textContent = 'Ошибка сети: ' + err.message;
            errorAlert.style.display = 'block';
        } finally {
            loader.style.display = 'none';
            btnText.textContent = '🔍 Обработать';
        }
    });

    document.getElementById('downloadBtn').addEventListener('click', async () => {
        window.location.href = '/download';
    });
</script>
</body>
</html>
