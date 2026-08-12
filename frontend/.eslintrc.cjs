/* eslint-env node */
module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  parser: 'vue-eslint-parser',
  parserOptions: {
    parser: '@typescript-eslint/parser',
    ecmaVersion: 'latest',
    sourceType: 'module',
  },
  plugins: ['vue', '@typescript-eslint'],
  extends: [
    'eslint:recommended',
    'plugin:vue/vue3-recommended',
    'plugin:@typescript-eslint/recommended',
    'prettier', // 关闭与 Prettier 冲突的格式规则
  ],
  rules: {
    // unplugin-auto-import 提供 ref/computed/watch 等全局, 不需要 import
    'no-undef': 'off',
    'vue/multi-word-component-names': 'off',
    // 类型相关: 先放宽容错, 聚焦明显问题
    '@typescript-eslint/no-explicit-any': 'off',
    '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    // Vue 模板风格: 放宽以免大规模改动
    'vue/max-attributes-per-line': 'off',
    'vue/singleline-html-element-content-newline': 'off',
    'vue/html-self-closing': 'off',
    'vue/attributes-order': 'off',
    // markdown 渲染用 v-html(marked 不净化, 模型输出视为可信); 如需更强安全可引入 DOMPurify
    'vue/no-v-html': 'off',
  },
  ignorePatterns: ['dist', 'node_modules', '*.d.ts', 'auto-imports.d.ts', 'components.d.ts'],
}
