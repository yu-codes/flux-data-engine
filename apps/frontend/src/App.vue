<template>
  <div id="app">
    <nav class="navbar">
      <div class="navbar-inner">
        <router-link to="/" class="brand">Flux Data Engine</router-link>
        <div class="nav-links">
          <router-link to="/">首頁</router-link>
          <router-link to="/typhoon/analysis">資料分析</router-link>
          <router-link to="/typhoon/methods">方法說明</router-link>
          <router-link to="/typhoon/experiments">預測結果</router-link>
          <router-link to="/typhoon/predict">即時預測</router-link>
        </div>
      </div>
    </nav>
    <div class="container">
      <router-view />
    </div>
    <footer class="footer">
      <p>Flux Data Engine v3.0.0 — 多災害類比預測引擎</p>
    </footer>
  </div>
</template>

<script setup>
</script>
