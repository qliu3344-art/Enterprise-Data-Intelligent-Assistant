import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/datasources',
  },
  {
    path: '/datasources',
    name: 'DataSourceList',
    component: () => import('@/pages/DataSourceListPage.vue'),
    meta: { title: '数据源管理' },
  },
  {
    path: '/datasources/:id',
    name: 'DataSourceDetail',
    component: () => import('@/pages/DataSourceDetailPage.vue'),
    meta: { title: '数据源详情' },
  },
  {
    path: '/datasources/:id/mapping',
    name: 'SchemaMapping',
    component: () => import('@/pages/SchemaMappingPage.vue'),
    meta: { title: '表头映射审核' },
  },
  {
    path: '/collect',
    name: 'CollectHistory',
    component: () => import('@/pages/CollectHistoryPage.vue'),
    meta: { title: '采集历史' },
  },
  {
    path: '/collect/:batchId',
    name: 'BatchDetail',
    component: () => import('@/pages/BatchDetailPage.vue'),
    meta: { title: '批次详情' },
  },
  {
    path: '/clean',
    name: 'CleanHistory',
    component: () => import('@/pages/CleanHistoryPage.vue'),
    meta: { title: '清洗日志' },
  },
  {
    path: '/clean/anomalies',
    name: 'AnomalyReview',
    component: () => import('@/pages/AnomalyReviewPage.vue'),
    meta: { title: '异常审核' },
  },
  {
    path: '/analysis',
    name: 'AnalysisDashboard',
    component: () => import('@/pages/AnalysisDashboardPage.vue'),
    meta: { title: '分析报表' },
  },
  {
    path: '/query',
    name: 'Query',
    component: () => import('@/pages/QueryPage.vue'),
    meta: { title: '智能查询' },
  },
  {
    path: '/rag',
    name: 'RAGDocuments',
    component: () => import('@/pages/RAGDocumentsPage.vue'),
    meta: { title: '制度文档库' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
