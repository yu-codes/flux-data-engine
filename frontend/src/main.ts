import { Dialog, Loading, Notify, Quasar } from 'quasar'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import '@quasar/extras/material-icons/material-icons.css'
import 'quasar/src/css/index.sass'
import './css/app.scss'

import App from './App.vue'
import router from './router'

createApp(App)
  .use(Quasar, {
    plugins: { Notify, Dialog, Loading },
    config: {
      dark: 'auto',
      notify: { position: 'top-right', timeout: 3500 },
    },
  })
  .use(createPinia())
  .use(router)
  .mount('#app')
