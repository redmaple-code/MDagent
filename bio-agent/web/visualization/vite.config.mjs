import {defineConfig} from 'vite';
export default defineConfig({base:'./',define:{'process.env.NODE_ENV':JSON.stringify('production')},build:{outDir:'dist',emptyOutDir:false,sourcemap:false,minify:true,
  lib:{entry:'src/index.mjs',formats:['es']},rolldownOptions:{output:{entryFileNames:'bio-view-[hash].js',chunkFileNames:'chunk-[hash].js',assetFileNames:'[name]-[hash][extname]'}}}});
