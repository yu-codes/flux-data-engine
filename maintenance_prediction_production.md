# Prompt：通用設備預防性維護分析產品設計與實作

你現在是一名資深 **Product Architect、Data Engineer、AI Engineer、Domain Expert 與 UI/UX Designer**。

請以「**通用設備預防性／預測性維護分析平台**」為目標，從零開始分析並設計一套可以真正落地的產品。

**本產品不使用 Machine Learning（ML）。**

不要設計：

* ML Model
* Machine Learning Training Pipeline
* Model Training Dataset
* ML Classification
* ML Regression
* Neural Network
* Deep Learning

系統主要透過：

```text
Rule-based Analysis
+
Statistical Analysis
+
Time-series Analysis
+
Threshold / Policy
+
Engineering / Physics Logic
+
Historical Event Analysis
+
LLM Reasoning
```

共同完成設備健康評估與維護決策。

---

# 一、核心目標

我要建立一個可以自動判斷：

* 設備目前是否需要維修
* 設備目前健康程度
* 設備故障風險
* 可能的故障原因
* 設備劣化趨勢
* 預估多久內可能需要維修
* 建議採取什麼維修措施
* 建議什麼時間前完成維修
* 為什麼系統做出這個判斷
* 判斷的信心程度

的設備維護分析模組。

**不要把系統設計成單純的 Sensor Anomaly Detection。**

系統應該從：

```text
Raw Data
↓
Data Quality
↓
Observation
↓
Statistical Analysis
↓
Rule / Threshold
↓
Engineering Logic
↓
Historical Comparison
↓
Health Assessment
↓
Risk Assessment
↓
Maintenance Decision
↓
LLM Reasoning
```

形成完整分析流程。

---

# 二、資料 Domain

請將設備資料抽象成通用 Domain。

**不能把 Schema 綁死在「馬達」、「泵浦」或任何單一設備。**

至少包含：

```text
Asset
├── Asset Profile
├── Telemetry
├── Operating Context
├── Maintenance History
├── Failure History
├── Maintenance Policy
└── Environment
```

---

## 1. Asset Profile

至少考慮：

* Asset ID
* Asset Type
* Manufacturer
* Model
* Installation Date
* Commission Date
* Location
* Rated Specifications
* Design Life
* Criticality
* Operating Limits

必須支援不同 Asset Type 擁有不同：

* Specifications
* Measurements
* Sensors
* Operating Limits
* Maintenance Policies

不要把設備屬性寫死。

---

# 三、Telemetry

支援通用時間序列資料：

```text
timestamp
asset_id
sensor_id
parameter
value
unit
sampling_frequency
data_quality
missing_flag
```

可以包含：

* Temperature
* Pressure
* Flow
* Current
* Voltage
* Power
* RPM
* Vibration
* Humidity
* Level
* Valve Position
* Bearing Temperature
* Oil Pressure
* Oil Temperature
* Lubrication Condition

但不要假設所有 Asset 都有這些欄位。

核心模型應該是：

```text
Observation
    ↓
Measurement
    ↓
Value
    ↓
Unit
    ↓
Timestamp
```

---

# 四、Operating Context

系統必須理解：

> 「為什麼設備現在會出現這個數值？」

例如：

```text
Motor Current = 90A
```

不能直接判斷異常。

必須結合：

* Operating State
* Load
* Runtime Hours
* Daily Runtime
* Start/Stop Count
* Cumulative Runtime
* Load Percentage
* Maintenance Mode
* Emergency Mode
* Startup
* Shutdown
* Idle
* Normal Operation
* Overload

例如：

```text
Current = 90A
Load = 95%
Operating State = Normal Operation
```

與：

```text
Current = 90A
Load = 30%
Operating State = Normal Operation
```

應該產生不同的分析結果。

---

# 五、Maintenance History

維修歷史必須描述：

```text
Symptom
↓
Failure / Degradation
↓
Root Cause
↓
Maintenance Action
↓
Parts Replacement
↓
Result
```

至少考慮：

* Maintenance ID
* Asset ID
* Maintenance Date
* Maintenance Type
* Failure Type
* Symptom
* Root Cause
* Action
* Parts Replaced
* Downtime
* Cost
* Technician
* Result

區分：

* Preventive Maintenance
* Corrective Maintenance
* Predictive Maintenance
* Emergency Maintenance
* Inspection

---

# 六、Failure History

Failure 不應與 Maintenance 完全混在一起。

Failure Event 至少考慮：

* Failure Date
* Failure Type
* Failure Mode
* Severity
* Symptoms
* Root Cause
* Downtime
* Impact
* Resolution
* Related Maintenance Event

系統應該能利用歷史事件進行：

```text
Current Condition
        ↓
Historical Pattern Comparison
        ↓
Similar Historical Events
        ↓
Potential Failure Mode
```

**不使用 ML。**

---

# 七、Maintenance Policy

系統必須支援傳統 Preventive Maintenance 規則。

例如：

```text
Runtime > 3000 hours
→ Lubrication

Runtime > 6000 hours
→ Bearing Inspection

Runtime > 12000 hours
→ Bearing Replacement
```

以及 Threshold：

```text
< 70°C
→ Normal

70–80°C
→ Warning

80–90°C
→ Critical

> 90°C
→ Emergency
```

支援：

* Time-based Maintenance
* Usage-based Maintenance
* Condition-based Maintenance
* Threshold Rules
* Inspection Rules
* Maintenance Interval
* Manufacturer Recommendation
* Engineering Rules

---

# 八、Statistical Analysis

請設計不依賴 ML 的統計分析能力。

至少考慮：

### Baseline

建立設備自身的正常基準：

```text
Historical Mean
Median
Standard Deviation
Percentile
Min / Max
Normal Range
```

---

### Trend Analysis

分析：

* Increasing Trend
* Decreasing Trend
* Stable
* Sudden Change
* Gradual Degradation

例如：

```text
62°C
64°C
68°C
73°C
79°C
```

即使尚未超過 Threshold，也應該識別：

```text
Temperature Increasing Trend
```

---

### Rate of Change

計算：

```text
ΔValue / ΔTime
```

例如：

```text
Temperature +17°C / 5 hours
```

---

### Rolling Statistics

支援：

* Moving Average
* Rolling Standard Deviation
* Rolling Median
* Rolling Percentile

---

### Deviation

例如：

```text
Current = 79°C
Historical Baseline = 65°C
Deviation = +21.5%
```

---

### Correlation

分析不同 Measurement 之間的關聯：

```text
Vibration ↑
+
Bearing Temperature ↑
+
Current Fluctuation ↑
```

作為設備劣化的證據。

---

# 九、Engineering / Physics Logic

不要只依賴統計。

系統應允許加入設備領域知識。

例如：

```text
Motor Temperature
≈ Ambient Temperature
+
Load Effect
+
Operating Condition
```

或者：

```text
High Load
+
High Temperature
+
Long Runtime
→ Thermal Stress
```

又例如：

```text
Vibration ↑
+
Bearing Temperature ↑
+
Lubrication Condition ↓
→ Bearing Degradation Suspected
```

請設計通用的：

```text
Condition
Rule
Factor
Relationship
Evidence
```

機制。

不要將這些邏輯寫死在程式碼。

---

# 十、Health Assessment

建立通用 Health Assessment。

例如：

```text
Health Score = 0–100
```

但不要單純使用固定公式。

請自行設計可以綜合：

* Threshold Status
* Trend
* Rate of Change
* Baseline Deviation
* Operating Condition
* Runtime
* Maintenance Interval
* Historical Events
* Criticality
* Data Quality

的 Health Assessment Framework。

輸出例如：

```json
{
  "health_score": 72,
  "health_status": "DEGRADED",
  "trend": "DECLINING",
  "confidence": 0.84
}
```

---

# 十一、Risk Assessment

不要使用 ML 來計算 Failure Probability。

請設計：

```text
Risk Assessment
=
Rule
+
Condition
+
Severity
+
Criticality
+
Historical Evidence
+
Degradation Indicators
```

可以使用 Risk Matrix：

```text
Likelihood × Consequence
```

例如：

```text
Likelihood:
LOW
MEDIUM
HIGH

Consequence:
LOW
MEDIUM
HIGH
CRITICAL
```

最後產生：

```text
LOW
MEDIUM
HIGH
CRITICAL
```

的 Risk Level。

請自行設計合理的 Risk Calculation Framework。

---

# 十二、RUL / Remaining Useful Life

由於本系統不使用 ML，請不要假裝可以精準預測：

> 「設備 18 天後一定故障。」

應該設計成：

```text
Estimated Maintenance Window
```

或：

```text
Estimated Remaining Service Window
```

透過：

* Maintenance Interval
* Runtime
* Degradation Trend
* Rate of Change
* Threshold Projection
* Engineering Rule
* Historical Maintenance Pattern

估算：

```text
Expected Maintenance Window
```

例如：

```text
Current degradation rate
+
Current operating hours
+
Maintenance threshold

→ Estimated threshold crossing date
```

並明確區分：

```text
Calculated
Estimated
Inferred
Unknown
```

避免製造虛假的精準度。

---

# 十三、LLM Reasoning

LLM 的角色不是直接從 Raw Sensor Data 猜設備是否故障。

LLM 應該處理：

```text
Structured Evidence
+
Rules
+
Statistics
+
Historical Events
+
Engineering Logic
```

然後進行：

```text
Evidence Synthesis
+
Explanation
+
Root Cause Reasoning
+
Maintenance Recommendation
```

例如：

```text
Evidence:

1. Vibration increased 32% in 7 days.
2. Bearing temperature is 14% above baseline.
3. Runtime reached 92% of maintenance interval.
4. Similar historical events involved bearing degradation.
5. Current load is within normal operating range.

↓

LLM Reasoning

↓

Likely bearing degradation.
Inspection is recommended before the next scheduled operation window.
```

**LLM 不可以自行創造不存在的 Sensor Data 或維修紀錄。**

所有推理都必須引用 Evidence。

---

# 十四、Maintenance Decision

最終系統輸出的核心不是：

```text
Anomaly = true
```

而是：

```text
Maintenance Decision
```

請設計完整 Schema，例如：

```json
{
  "maintenance_required": true,
  "health_score": 62,
  "health_status": "DEGRADED",
  "risk_level": "HIGH",
  "risk_factors": [],
  "estimated_maintenance_window": {
    "start": "2026-09-03",
    "end": "2026-09-05"
  },
  "recommended_action": "Inspect bearing",
  "priority": "HIGH",
  "confidence": 0.86,
  "reasons": [],
  "evidence": [],
  "triggered_rules": [],
  "historical_comparisons": [],
  "data_quality": {},
  "llm_reasoning": ""
}
```

請自行重新設計更合理、更通用的 Schema。

---

# 十五、Explainability

任何重要結論都必須能回答：

> Why?

例如：

```text
Risk Level: HIGH

Reasons:

• Vibration increased 32% within 7 days.
• Bearing temperature exceeded historical baseline.
• Operating hours reached 92% of maintenance interval.
• Current operating load is normal, reducing the likelihood that the abnormality is load-induced.
• Similar historical maintenance events were associated with bearing degradation.
```

請設計：

* Evidence
* Reason
* Contributing Factors
* Historical Comparison
* Rule Trigger
* Statistical Evidence
* Engineering Evidence
* Confidence
* Data Quality

---

# 十六、完整分析架構

請設計成：

```text
                    ┌── Rule Engine
                    │
                    ├── Statistical Engine
                    │
                    ├── Trend Analysis
                    │
Telemetry ───────────┤
                    ├── Engineering Logic
                    │
                    ├── Historical Analysis
                    │
                    └── Policy Evaluation
                              ↓
                       Health Assessment
                              ↓
                        Risk Assessment
                              ↓
                     Maintenance Window
                              ↓
                    Maintenance Decision
                              ↓
                       LLM Reasoning
                              ↓
                  Recommendation / Explanation
```

**禁止加入 ML Layer。**

---

# 十七、產品 UI

請把它當成真正的 Enterprise Product，而不是單純 Dashboard。

至少設計：

## Asset Overview

顯示：

* Asset Status
* Health Score
* Risk Level
* Current Condition
* Maintenance Status
* Last Maintenance
* Next Maintenance
* Maintenance Window
* Active Alerts

---

## Asset Detail

### Overview

```text
Health
Risk
Maintenance Status
Current Condition
```

### Telemetry

提供：

* Temperature
* Pressure
* Vibration
* Current
* Power
* Other Measurements

支援：

* 時間範圍
* Baseline
* Threshold
* Trend
* Anomaly Region
* Maintenance Event
* Failure Event

---

## Maintenance Analysis

顯示：

```text
Maintenance Required
Risk Level
Health Score
Maintenance Window
Recommended Action
Priority
Confidence
```

並提供：

```text
Why?
```

讓使用者看到完整 Evidence。

---

## Maintenance Timeline

將：

```text
Telemetry
↓
Condition Change
↓
Rule Trigger
↓
Risk Increase
↓
Recommendation
↓
Inspection
↓
Maintenance
↓
Recovery
```

整合成 Timeline。

---

# 十八、通用 Domain Model

不要建立：

```text
MotorMaintenance
PumpMaintenance
ValveMaintenance
```

這類專用模型。

應該抽象成：

```text
Asset
Observation
Measurement
Feature
Condition
Rule
Policy
Assessment
Risk
Event
Failure
Maintenance
Recommendation
Evidence
```

讓同一套架構可以支援：

```text
Pump
Motor
Valve
Compressor
Generator
Transformer
HVAC
Infrastructure
Industrial Equipment
```

---

# 十九、Data Quality

設備資料一定會存在：

* Missing Data
* Sensor Failure
* Outlier
* Duplicate
* Wrong Unit
* Timestamp Error
* Sensor Drift
* Irregular Sampling

請建立 Data Quality Layer。

必須避免：

```text
Sensor Failure
↓
False Anomaly
↓
False Maintenance Decision
```

---

# 二十、Cold Start

新設備可能完全沒有：

* Maintenance History
* Failure History
* Historical Telemetry

請設計 Cold Start Strategy。

優先使用：

```text
Manufacturer Specification
+
Engineering Rules
+
Maintenance Policy
+
Operating Limits
+
Generic Statistical Baseline
```

當資料逐漸累積後，再建立：

```text
Asset-specific Baseline
```

---

# 二十一、產品化要求

請考慮：

* False Positive
* False Negative
* Alert Fatigue
* Data Quality
* Sensor Drift
* Baseline Drift
* Rule Versioning
* Policy Versioning
* Explainability
* Confidence
* Human Review
* Maintenance Cost
* Downtime Cost
* Asset Criticality
* Risk-based Maintenance
* Audit Trail

尤其：

> Critical Asset 與 Low Criticality Asset 不應該使用完全相同的 Maintenance Decision Threshold。

---

# 二十二、請自行設計完整 Domain Architecture

產生：

```text
Entity
Relationship
Primary Key
Foreign Key
Enum
JSON Schema
Time-series Schema
Event Schema
```

並說明：

```text
Asset
 ↓
Observation
 ↓
Analysis
 ↓
Assessment
 ↓
Risk
 ↓
Recommendation
 ↓
Maintenance Event
```

之間的關係。

---

# 二十三、請自行設計 API

請根據你的 Domain Model 設計 REST API。

至少需要涵蓋：

```text
Asset
Telemetry
Analysis
Health
Risk
Maintenance
Failure
Policy
Rule
Recommendation
Timeline
Evidence
```

不要盲目使用固定 Endpoint。

請自行做 API Design Decision。

---

# 二十四、請自行設計資料流

完整設計：

```text
Data Source
    ↓
Ingestion
    ↓
Validation
    ↓
Time Series Storage
    ↓
Data Quality
    ↓
Analysis Engine
    ↓
Rule / Statistical / Engineering Analysis
    ↓
Assessment
    ↓
Decision Engine
    ↓
LLM Reasoning
    ↓
Recommendation
    ↓
UI / API
```

說明每個 Layer 的：

* Responsibility
* Input
* Output
* Dependencies
* Failure Handling

---

# 二十五、請自行設計 Analysis Engine

請建立通用分析框架，使未來新增分析方法時不需要修改核心架構。

例如：

```text
Analysis Engine

├── Threshold Analyzer
├── Trend Analyzer
├── Baseline Analyzer
├── Statistical Analyzer
├── Correlation Analyzer
├── Runtime Analyzer
├── Maintenance Interval Analyzer
├── Historical Event Analyzer
├── Engineering Rule Analyzer
└── Risk Analyzer
```

不要把這些分析器與特定設備綁定。

---

# 二十六、Model 不代表 Machine Learning

本產品中的「Model」可以是：

```text
Domain Model
Rule Model
Calculation Model
Statistical Model
Physics Model
Risk Model
Decision Model
```

**不要因為看到 Model 就加入 Machine Learning。**

---

# 二十七、Implementation

如果目前存在既有專案：

1. 先理解現有架構
2. 找出可以重用的 Domain
3. 找出可以重用的 Data Model
4. 找出既有 Analysis / Execution 能力
5. 找出既有 UI 元件
6. 不要無理由重構

然後提出：

* Backend Architecture
* Frontend Architecture
* Database
* Time-series Storage
* Analysis Engine
* Rule Engine
* Decision Engine
* LLM Integration
* Background Jobs
* Event Processing

---

# 二十八、MVP

最後自行定義：

```text
MVP
↓
V1
↓
V2
↓
Advanced Maintenance Intelligence
```

明確說明：

* MVP 必須做什麼
* 可以延後什麼
* 哪些功能未來再做
* 哪些功能現在做會增加不必要複雜度

---

# 二十九、最重要的要求

**不要只是重新整理需求。**

我要你真正「做產品設計決策」。

如果我的需求有不合理的地方：

> 直接修改，並說明為什麼。

如果有多種架構：

> 選擇你認為最適合產品化的一種。

不要只是列出 A / B / C 而不做決策。

最終目標不是：

```text
Sensor Dashboard
```

也不是：

```text
Anomaly Detection System
```

而是：

```text
Generic Asset Intelligence
        ↓
Condition Assessment
        ↓
Risk Assessment
        ↓
Maintenance Decision
        ↓
Explainable Recommendation
```

其中：

**Predictive / Preventive Maintenance 是第一個 Application，而不是整個平台的 Domain Boundary。**

最終設計必須可以直接作為：

```text
Database Design
+
Backend Development
+
Analysis Engine
+
AI / LLM Integration
+
Frontend Development
```

的實作基礎。
