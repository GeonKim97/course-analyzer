const statusBanner = document.getElementById("statusBanner");
const sectionAuth = document.getElementById("sectionAuth");
const authHint = document.getElementById("authHint");
const passwordInput = document.getElementById("passwordInput");
const btnLogin = document.getElementById("btnLogin");
const btnLogout = document.getElementById("btnLogout");
const workspaceSections = document.getElementById("workspaceSections");

const fileInput = document.getElementById("fileInput");
const btnInspect = document.getElementById("btnInspect");
const questionInput = document.getElementById("questionInput");
const btnAsk = document.getElementById("btnAsk");
const askPlanInfo = document.getElementById("askPlanInfo");
const btnSuggest = document.getElementById("btnSuggest");
const btnRun = document.getElementById("btnRun");
const btnExplain = document.getElementById("btnExplain");

const reportToolbar = document.getElementById("reportToolbar");
const btnModeEdit = document.getElementById("btnModeEdit");
const btnModePreview = document.getElementById("btnModePreview");
const btnCopyReport = document.getElementById("btnCopyReport");
const btnDownloadReport = document.getElementById("btnDownloadReport");
const reportEditor = document.getElementById("reportEditor");
const reportPreview = document.getElementById("reportPreview");

const sectionInspect = document.getElementById("sectionInspect");
const sectionSuggest = document.getElementById("sectionSuggest");
const sectionResults = document.getElementById("sectionResults");
const sectionReport = document.getElementById("sectionReport");

const inspectStats = document.getElementById("inspectStats");
const warningList = document.getElementById("warningList");
const missingTableWrap = document.getElementById("missingTableWrap");
const suggestList = document.getElementById("suggestList");
const resultsContainer = document.getElementById("resultsContainer");

let currentSuggestions = [];
let activeCharts = [];
let currentFilename = "report";
let reportViewMode = "preview"; // 'edit' | 'preview'

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setBanner(type, message) {
  statusBanner.className = `status-banner ${type}`;
  statusBanner.textContent = message;
  statusBanner.classList.remove("hidden");
}

function setBusy(isBusy, message = "처리 중...") {
  const buttons = [btnInspect, btnAsk, btnSuggest, btnRun, btnExplain];
  buttons.forEach((btn) => {
    btn.disabled = isBusy;
  });
  if (isBusy) {
    setBanner("info", message);
  }
}

function applyAuthUI(authenticated, passwordConfigured, minLength = 8) {
  if (authenticated) {
    sectionAuth.classList.add("hidden");
    workspaceSections.classList.remove("hidden");
    btnLogout.classList.remove("hidden");
  } else {
    sectionAuth.classList.remove("hidden");
    workspaceSections.classList.add("hidden");
    btnLogout.classList.add("hidden");
    if (!passwordConfigured) {
      authHint.textContent = `서버의 .env 파일에 ${minLength}자리 이상의 APP_PASSWORD를 설정한 뒤 서버를 다시 시작해 주세요.`;
      btnLogin.disabled = true;
    } else {
      authHint.textContent = `사용하려면 ${minLength}자리 이상의 비밀번호를 입력해 주세요.`;
      btnLogin.disabled = false;
    }
  }
}

async function checkAuthStatus() {
  try {
    const res = await fetch("/api/auth/status");
    const data = await res.json();
    console.log("[auth] 상태 확인:", data);
    applyAuthUI(data.authenticated, data.password_configured, data.min_length);
  } catch (err) {
    console.error("[auth] 상태 확인 실패:", err);
  }
}

async function handleLogin() {
  const pw = passwordInput.value;
  if (pw.length < 8) {
    setBanner("error", "비밀번호는 8자리 이상 입력해야 합니다.");
    return;
  }
  console.log("[auth] 잠금 해제 시도");
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "비밀번호 확인에 실패했습니다.");
    }
    passwordInput.value = "";
    applyAuthUI(true, true, 8);
    setBanner("success", "잠금이 해제되었습니다. 엑셀(.xls) 파일을 올려 시작하세요.");
  } catch (err) {
    console.error("[auth] 로그인 오류:", err);
    setBanner("error", err.message);
  }
}

btnLogin.addEventListener("click", handleLogin);
passwordInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") handleLogin();
});

btnLogout.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  applyAuthUI(false, true, 8);
  setBanner("info", "앱이 잠겼습니다.");
});

function renderTable(rows) {
  if (!rows || rows.length === 0) {
    return "<p class='hint'>표시할 데이터가 없습니다.</p>";
  }
  const headers = Object.keys(rows[0]);
  const headHtml = headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("");
  const bodyHtml = rows
    .map((row) => {
      const cells = headers
        .map((h) => `<td>${escapeHtml(row[h] === null || row[h] === undefined ? "" : row[h])}</td>`)
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  return `<table><thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`;
}

function renderInspectResult(data) {
  const s = data.course_stats;
  const statItems = [
    { label: "전체 행 수", value: `${s.total_rows.toLocaleString()}행` },
    { label: "정상 개설 행", value: `${s.active_rows.toLocaleString()}행` },
    { label: "폐강 행(비율)", value: `${s.cancelled_rows}행 (${s.cancellation_rate_pct}%)` },
    { label: "고유 과목+분반", value: `${s.unique_course_section_count.toLocaleString()}개` },
    { label: "단과대 수", value: `${s.college_count}개` },
    { label: "제한인원 0명(정상)", value: `${s.zero_capacity_active_count}행` },
  ];

  inspectStats.innerHTML = statItems
    .map(
      (item) => `
      <div class="stat-box">
        <div class="stat-label">${escapeHtml(item.label)}</div>
        <div class="stat-value">${escapeHtml(item.value)}</div>
      </div>`
    )
    .join("");

  warningList.innerHTML = (data.warnings || [])
    .map(
      (w) => `
      <li>
        <span class="warning-tag">${escapeHtml(w.category)}</span>
        <span>${escapeHtml(w.message)}</span>
      </li>`
    )
    .join("");

  const missingRows = (data.missing_summary || []).map((m) => ({
    열이름: m.column,
    빈칸수: m.missing_count,
    채워진수: m.filled_count,
    비고: m.note,
  }));
  missingTableWrap.innerHTML = renderTable(missingRows);

  sectionInspect.classList.remove("hidden");
  sectionSuggest.classList.remove("hidden");
  sectionResults.classList.add("hidden");
  sectionReport.classList.add("hidden");
  askPlanInfo.classList.add("hidden");
  suggestList.innerHTML = "";
  btnRun.classList.add("hidden");
  reportToolbar.classList.add("hidden");
  reportEditor.classList.add("hidden");
  reportPreview.classList.add("hidden");
}

function renderResults(results) {
  activeCharts.forEach((ch) => ch.destroy());
  activeCharts = [];

  resultsContainer.innerHTML = (results || [])
    .map((r, idx) => {
      const chartHtml = r.chart
        ? `<div class="chart-box"><canvas id="chart_${idx}"></canvas></div>`
        : "";
      return `
        <div class="result-block">
          <h3>${idx + 1}. ${escapeHtml(r.title)}</h3>
          <div class="result-meta">
            <div><strong>기준(정의):</strong> ${escapeHtml(r.definition)}</div>
            <div><strong>사용한 행 수:</strong> ${escapeHtml(r.used_rows.toLocaleString())}행 | <strong>제외한 행 수:</strong> ${escapeHtml(r.excluded_rows.toLocaleString())}행 (${escapeHtml(r.excluded_reason)})</div>
            <div><strong>사용한 열:</strong> ${escapeHtml((r.used_columns || []).join(", "))} | <strong>단위:</strong> ${escapeHtml(r.unit)}</div>
            <div><strong>주의사항:</strong> ${escapeHtml(r.cautions)}</div>
          </div>
          ${chartHtml}
          <div class="table-wrap">${renderTable(r.table)}</div>
        </div>`;
    })
    .join("");

  if (typeof Chart !== "undefined") {
    (results || []).forEach((r, idx) => {
      if (!r.chart) return;
      const canvas = document.getElementById(`chart_${idx}`);
      if (!canvas) return;
      const chartInstance = new Chart(canvas, {
        type: r.chart.type || "bar",
        data: {
          labels: r.chart.labels,
          datasets: [
            {
              label: r.chart.dataset_label || r.title,
              data: r.chart.values,
              backgroundColor: "rgba(37, 99, 235, 0.7)",
              borderColor: "rgba(29, 78, 216, 1)",
              borderWidth: 1,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: { y: { beginAtZero: true } },
        },
      });
      activeCharts.push(chartInstance);
    });
  }

  sectionResults.classList.remove("hidden");
  sectionReport.classList.remove("hidden");
}

function switchReportView(mode) {
  reportViewMode = mode;
  if (mode === "edit") {
    btnModeEdit.classList.add("active");
    btnModePreview.classList.remove("active");
    reportEditor.classList.remove("hidden");
    reportPreview.classList.add("hidden");
  } else {
    btnModePreview.classList.add("active");
    btnModeEdit.classList.remove("active");
    const mdText = reportEditor.value || "";
    if (typeof marked !== "undefined") {
      reportPreview.innerHTML = marked.parse(mdText);
    } else {
      reportPreview.textContent = mdText;
    }
    reportEditor.classList.add("hidden");
    reportPreview.classList.remove("hidden");
  }
}

btnInspect.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) {
    setBanner("error", "먼저 엑셀(.xls) 파일을 선택해 주세요.");
    return;
  }
  if (!file.name.toLowerCase().endsWith(".xls")) {
    setBanner("error", "지원하지 않는 파일 형식입니다. .xls 파일만 선택해 주세요.");
    return;
  }

  console.log("[inspect] 파일 업로드 및 진단 시작:", file.name);
  setBusy(true, "처리 중... 엑셀 파일을 읽고 진단하고 있습니다.");

  try {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/inspect", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "파일 진단에 실패했습니다.");
    }
    currentFilename = data.filename || "report";
    renderInspectResult(data);
    console.log("[inspect] 진단 완료:", data.rows, "행, 경고", data.warnings.length, "건");
    setBanner("success", `진단 완료: ${data.filename} (${data.rows.toLocaleString()}행 × ${data.columns}열)`);
  } catch (err) {
    console.error("[inspect] 오류:", err);
    setBanner("error", err.message);
  } finally {
    setBusy(false);
  }
});

btnAsk.addEventListener("click", async () => {
  const question = questionInput.value.trim();
  if (!question) {
    setBanner("error", "분석하고 싶은 질문을 입력해 주세요.");
    return;
  }

  console.log("[ask] 자유 질문 분석 시작:", question);
  setBusy(true, "처리 중... AI가 질문에 맞는 도구를 고르고 pandas로 계산 중입니다.");

  try {
    const res = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "질문 분석에 실패했습니다.");
    }

    askPlanInfo.innerHTML = `<strong>AI가 선택한 도구:</strong> <code>${escapeHtml(data.plan.tool_name)}</code> | <strong>설명:</strong> ${escapeHtml(data.plan.explanation)}`;
    askPlanInfo.classList.remove("hidden");

    renderResults(data.results);
    console.log("[ask] 질문 계산 완료:", data.plan);
    setBanner("success", "질문 분석이 완료되었습니다. 아래 계산 결과를 확인하세요.");
  } catch (err) {
    console.error("[ask] 오류:", err);
    setBanner("error", err.message);
  } finally {
    setBusy(false);
  }
});

questionInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") btnAsk.click();
});

btnSuggest.addEventListener("click", async () => {
  console.log("[suggest] AI 분석 제안 요청 시작");
  setBusy(true, "처리 중... AI에게 가능한 분석을 추천받고 있습니다.");

  try {
    const res = await fetch("/suggest", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "AI 제안을 받아오지 못했습니다.");
    }

    currentSuggestions = data.suggestions || [];
    suggestList.innerHTML = currentSuggestions
      .map(
        (item, idx) => `
        <label class="suggest-item">
          <input type="checkbox" class="suggest-checkbox" value="${idx}" checked />
          <div>
            <div class="suggest-title">${escapeHtml(item.label)} (<code>${escapeHtml(item.tool_name)}</code>)</div>
            <div class="suggest-meta">추천 이유: ${escapeHtml(item.reason)}</div>
            <div class="suggest-meta">사용할 열: ${escapeHtml((item.columns || []).join(", "))}</div>
          </div>
        </label>`
      )
      .join("");

    btnRun.classList.remove("hidden");
    console.log("[suggest] 제안 수신 완료:", currentSuggestions.map((s) => s.tool_name));
    setBanner("success", `AI가 ${currentSuggestions.length}개의 분석 도구를 제안했습니다. 원하는 항목을 선택해 계산하세요.`);
  } catch (err) {
    console.error("[suggest] 오류:", err);
    setBanner("error", err.message);
  } finally {
    setBusy(false);
  }
});

btnRun.addEventListener("click", async () => {
  const checkedBoxes = Array.from(document.querySelectorAll(".suggest-checkbox:checked"));
  if (checkedBoxes.length === 0) {
    setBanner("error", "실행할 분석 항목을 하나 이상 체크해 주세요.");
    return;
  }

  const selected = checkedBoxes.map((box) => {
    const item = currentSuggestions[Number(box.value)];
    return { tool_name: item.tool_name, params: item.params || {} };
  });

  console.log("[run] 선택한 분석 계산 시작:", selected.map((s) => s.tool_name));
  setBusy(true, "처리 중... 파이썬(pandas)으로 정확히 계산하고 있습니다.");

  try {
    const res = await fetch("/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "계산 실행에 실패했습니다.");
    }

    renderResults(data.results);
    console.log("[run] 계산 완료:", data.results.length, "개 결과");
    setBanner("success", `계산 완료: ${data.results.length}개의 분석 결과가 나왔습니다. 이제 보고서를 생성해 보세요.`);
  } catch (err) {
    console.error("[run] 오류:", err);
    setBanner("error", err.message);
  } finally {
    setBusy(false);
  }
});

btnExplain.addEventListener("click", async () => {
  const modeInput = document.querySelector('input[name="reportMode"]:checked');
  const mode = modeInput ? modeInput.value : "B";

  console.log("[explain] 보고서 생성 요청 시작 - 모드:", mode);
  setBusy(true, `처리 중... 모드 ${mode} 보고서를 작성하고 있습니다.`);

  try {
    const res = await fetch("/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "보고서 생성에 실패했습니다.");
    }

    reportEditor.value = data.report || "";
    reportToolbar.classList.remove("hidden");
    switchReportView("preview");

    console.log("[explain] 보고서 생성 완료 - 모드:", mode, "길이:", reportEditor.value.length, "자");
    setBanner("success", `모드 ${mode} 보고서가 생성되었습니다. '직접 수정' 또는 '미리보기'로 확인하세요.`);
  } catch (err) {
    console.error("[explain] 오류:", err);
    setBanner("error", err.message);
  } finally {
    setBusy(false);
  }
});

btnModeEdit.addEventListener("click", () => switchReportView("edit"));
btnModePreview.addEventListener("click", () => switchReportView("preview"));

btnCopyReport.addEventListener("click", async () => {
  if (!reportEditor.value) return;

  try {
    if (reportViewMode === "preview") {
      // 미리보기 상태: 워드/이메일용 HTML과 카톡/메모장용 깔끔한 텍스트를 함께 복사합니다.
      const cleanText = reportPreview.innerText.trim();
      const htmlContent = `<div>${reportPreview.innerHTML}</div>`;

      if (typeof ClipboardItem !== "undefined" && navigator.clipboard.write) {
        const item = new ClipboardItem({
          "text/plain": new Blob([cleanText], { type: "text/plain" }),
          "text/html": new Blob([htmlContent], { type: "text/html" }),
        });
        await navigator.clipboard.write([item]);
      } else {
        await navigator.clipboard.writeText(cleanText);
      }
      console.log("[copy] 미리보기(서식+깔끔한 텍스트) 복사 완료");
      setBanner("success", "보기 좋은 서식으로 복사했습니다! 카톡·이메일·워드에 바로 붙여넣어 보세요.");
    } else {
      await navigator.clipboard.writeText(reportEditor.value);
      console.log("[copy] 마크다운 원문 복사 완료");
      setBanner("success", "수정된 보고서 원문(마크다운)을 복사했습니다.");
    }
  } catch (err) {
    console.error("[copy] 복사 실패:", err);
    setBanner("error", "복사에 실패했습니다. 보고서 영역에서 직접 드래그해 복사해 주세요.");
  }
});

btnDownloadReport.addEventListener("click", () => {
  const content = reportEditor.value;
  if (!content) return;
  const modeInput = document.querySelector('input[name="reportMode"]:checked');
  const mode = modeInput ? modeInput.value : "B";
  const baseName = currentFilename.replace(/\.xls$/i, "");
  const outName = `${baseName}_보고서_모드${mode}.md`;

  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = outName;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);

  console.log("[download] 마크다운 파일 다운로드:", outName);
  setBanner("success", `${outName} 파일로 다운로드했습니다.`);
});

checkAuthStatus();
