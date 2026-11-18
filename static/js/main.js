// NeuroForge BioMaterials — frontend demo logic

document.addEventListener('DOMContentLoaded', () => {
  // --- Mobile menu ---
  const toggle = document.querySelector('.menu-toggle');
  const nav = document.querySelector('.nav');

  if (toggle && nav) {
    toggle.addEventListener('click', () => {
      const isOpen = nav.style.display === 'flex';
      nav.style.display = isOpen ? 'none' : 'flex';
      if (!isOpen) {
        nav.style.flexDirection = 'column';
        nav.style.position = 'absolute';
        nav.style.top = '64px';
        nav.style.left = '0';
        nav.style.right = '0';
        nav.style.background = '#F8FAFC';
        nav.style.padding = '20px 24px';
        nav.style.borderBottom = '1px solid #E2E8F0';
        nav.style.gap = '16px';
        nav.style.zIndex = '99';
      }
    });
  }

  document.querySelectorAll('.nav a').forEach(link => {
    link.addEventListener('click', () => {
      if (window.innerWidth <= 900 && nav) nav.style.display = 'none';
    });
  });

  // --- Demo upload & process ---
  const uploadZone = document.getElementById('upload-zone');
  const fileInput = document.getElementById('file-input');
  const uploadContent = document.getElementById('upload-content');
  const uploadPreview = document.getElementById('upload-preview');
  const previewImg = document.getElementById('preview-img');
  const changeBtn = document.getElementById('change-btn');
  const ctZone = document.getElementById('ct-zone');
  const ctInput = document.getElementById('ct-input');
  const ctContent = document.getElementById('ct-content');
  const ctPreview = document.getElementById('ct-preview');
  const ctName = document.getElementById('ct-name');
  const ctChange = document.getElementById('ct-change');
  const simulateBox = document.getElementById('simulate-defect');
  const processBtn = document.getElementById('process-btn');
  const statusText = document.getElementById('status-text');
  const progressBox = document.getElementById('progress-box');
  const progressFill = document.getElementById('progress-fill');
  const progressLabel = document.getElementById('progress-label');
  const resultsBox = document.getElementById('results-box');
  const downloadStl = document.getElementById('download-stl');
  const patientForm = document.getElementById('patient-form');
  const viewerBox = document.getElementById('viewer-box');
  const errorBox = document.getElementById('error-box');

  let mode = 'ct';
  let imageFile = null;
  let ctFile = null;

  if (!uploadZone || !ctZone) return;

  // --- переключение режимов ---
  document.querySelectorAll('.mode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      mode = btn.dataset.mode;
      document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b === btn));
      document.getElementById('ct-mode').classList.toggle('hidden', mode !== 'ct');
      document.getElementById('image-mode').classList.toggle('hidden', mode !== 'image');
      updateButton();
      resultsBox.classList.add('hidden');
      statusText.textContent = '';
    });
  });

  function updateButton() {
    processBtn.disabled = mode === 'ct' ? !ctFile : !imageFile;
  }

  // --- загрузка картинки ---
  uploadZone.addEventListener('click', (e) => {
    if (e.target === changeBtn) return;
    fileInput.click();
  });
  changeBtn?.addEventListener('click', (e) => { e.stopPropagation(); fileInput.click(); });
  setupDrop(uploadZone, (file) => { if (file.type.startsWith('image/')) handleImage(file); });
  fileInput.addEventListener('change', () => { if (fileInput.files[0]) handleImage(fileInput.files[0]); });

  function handleImage(file) {
    imageFile = file;
    previewImg.src = URL.createObjectURL(file);
    uploadContent.classList.add('hidden');
    uploadPreview.classList.remove('hidden');
    statusText.textContent = file.name;
    resultsBox.classList.add('hidden');
    progressBox.classList.add('hidden');
    updateButton();
  }

  // --- загрузка КТ ---
  ctZone.addEventListener('click', (e) => {
    if (e.target === ctChange) return;
    ctInput.click();
  });
  ctChange?.addEventListener('click', (e) => { e.stopPropagation(); ctInput.click(); });
  setupDrop(ctZone, handleCT);
  ctInput.addEventListener('change', () => { if (ctInput.files[0]) handleCT(ctInput.files[0]); });

  function handleCT(file) {
    ctFile = file;
    ctName.textContent = file.name + ' · ' + (file.size / 1048576).toFixed(1) + ' МБ';
    ctContent.classList.add('hidden');
    ctPreview.classList.remove('hidden');
    statusText.textContent = '';
    resultsBox.classList.add('hidden');
    progressBox.classList.add('hidden');
    updateButton();
  }

  function setupDrop(zone, onFile) {
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.classList.remove('dragover');
      const file = e.dataTransfer.files[0];
      if (file) onFile(file);
    });
  }

  // --- запуск ---
  processBtn.addEventListener('click', async () => {
    const file = mode === 'ct' ? ctFile : imageFile;
    if (!file) return;

    // Проверка анкеты пациента
    let formOk = true;
    patientForm.querySelectorAll('.field').forEach(f => {
      const input = f.querySelector('input, select');
      const bad = !input.checkValidity();
      f.classList.toggle('invalid', bad);
      if (bad) formOk = false;
    });
    if (!formOk) {
      statusText.textContent = 'Заполните данные пациента (возраст 18–95, рост 120–230 см, вес 30–250 кг).';
      patientForm.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }

    processBtn.disabled = true;
    errorBox.classList.add('hidden');
    resultsBox.classList.add('hidden');
    progressBox.classList.remove('hidden');
    progressFill.style.width = '8%';
    progressLabel.textContent = mode === 'ct' ? 'Загрузка КТ…' : 'Загрузка снимка…';
    statusText.textContent = '';

    const stages = mode === 'ct' ? [
      { pct: 20, label: 'Чтение КТ и приведение к сетке 2 мм…' },
      { pct: 35, label: 'Выделение кости черепа…' },
      { pct: 50, label: 'Поиск дефекта свода…' },
      { pct: 65, label: 'Восстановление контура черепа…' },
      { pct: 80, label: 'Построение пористой решётки (гироид)…' },
      { pct: 92, label: 'Формирование STL…' },
    ] : [
      { pct: 25, label: 'Сегментация области…' },
      { pct: 45, label: 'Генерация геометрии (gyroid)…' },
      { pct: 65, label: 'Расчёт прочности (surrogate)…' },
      { pct: 80, label: 'Подбор материала…' },
      { pct: 92, label: 'Формирование STL…' },
    ];

    let stageIdx = 0;
    const progressInterval = setInterval(() => {
      if (stageIdx < stages.length) {
        progressFill.style.width = stages[stageIdx].pct + '%';
        progressLabel.textContent = stages[stageIdx].label;
        stageIdx++;
      }
    }, mode === 'ct' ? 2500 : 700);

    try {
      const form = new FormData();
      form.append('file', file);
      const pd = new FormData(patientForm);
      for (const [k, v] of pd.entries()) form.append(k, v);
      form.set('smoker', pd.has('smoker') ? 'true' : 'false');
      if (mode === 'ct') form.set('simulate_defect', simulateBox.checked ? 'true' : 'false');

      const res = await fetch(mode === 'ct' ? '/api/process_ct' : '/api/process', {
        method: 'POST',
        body: form,
      });

      clearInterval(progressInterval);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(typeof err.detail === 'string' ? err.detail : 'Проверьте введённые данные');
      }
      progressFill.style.width = '100%';
      progressLabel.textContent = 'Готово';

      const data = await res.json();
      await showResults(data);
    } catch (err) {
      clearInterval(progressInterval);
      progressLabel.textContent = 'Ошибка';
      let msg = err.message || 'Не удалось обработать снимок.';
      if (err instanceof TypeError) {
        msg = 'Соединение с сервером оборвалось. Возможно, серверу не хватило памяти или он перезапускается — подождите минуту и повторите. (' + msg + ')';
      }
      errorBox.textContent = msg;
      errorBox.classList.remove('hidden');
      errorBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      console.error(err);
    } finally {
      updateButton();
      setTimeout(() => progressBox.classList.add('hidden'), 800);
    }
  });

  const setText = (id, v) => { document.getElementById(id).textContent = v; };

  async function showResults(data) {
    resultsBox.classList.remove('hidden');
    const isCT = data.mode === 'ct_skull';
    document.querySelectorAll('.ct-only').forEach(el => el.classList.toggle('hidden', !isCT));

    setText('res-material', data.material_recommendation?.material || 'PCL');
    setText('res-structure', data.material_recommendation?.structure || 'Gyroid / TPMS');
    setText('res-modulus', (data.metrics?.young_modulus_mpa ?? '—') + ' МПа');
    setText('res-porosity',
      data.metrics?.porosity_target != null ? Math.round(data.metrics.porosity_target * 100) + '%' : '—');
    setText('res-fit', data.metrics?.fit_error_mm != null ? data.metrics.fit_error_mm + ' мм' : '—');
    setText('res-time', data.metrics?.design_time_sec != null ? data.metrics.design_time_sec + ' сек' : '—');
    setText('res-bmi', data.metrics?.bmi ?? '—');
    setText('res-load', data.metrics?.load_index ?? '—');
    setText('res-bone', data.metrics?.bone_quality_index ?? '—');

    if (isCT) {
      const c = data.ct || {};
      setText('res-defect', (c.defect_size_mm || []).join(' × ') + ' мм (' + c.defect_area_cm2 + ' см²)');
      setText('res-thick', c.thickness_mm + ' мм');
      setText('res-vol', data.metrics.implant_volume_cm3 + ' см³ / ' + data.metrics.implant_mass_g + ' г');
    }

    const adjBox = document.getElementById('adjustments-box');
    const adjList = document.getElementById('adjustments-list');
    adjList.innerHTML = '';
    (data.adjustments || []).forEach(t => {
      const li = document.createElement('li');
      li.textContent = t;
      adjList.appendChild(li);
    });
    adjBox.classList.toggle('hidden', !(data.adjustments || []).length);

    setText('res-disclaimer', data.disclaimer || 'Research Use Only. Упрощённый прототип.');

    if (data.mesh?.stl_url) {
      downloadStl.href = data.mesh.stl_url;
      downloadStl.classList.remove('hidden');
    } else {
      downloadStl.classList.add('hidden');
    }

    // 3D-просмотр
    viewerBox.classList.add('hidden');
    if (isCT && data.mesh?.stl_url && window.NFViewer) {
      try {
        const r = await fetch(data.mesh.stl_url);
        const buf = await r.arrayBuffer();
        viewerBox.classList.remove('hidden');
        window.NFViewer.show(buf, data.preview?.skull_points);
      } catch (e) {
        console.error('Не удалось показать 3D-модель', e);
      }
    }

    resultsBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
});
