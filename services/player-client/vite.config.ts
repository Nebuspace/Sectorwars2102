import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import dns from 'dns'

// This is critical: configure DNS to use IPv4 instead of IPv6
dns.setDefaultResultOrder('ipv4first')

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    // Completely disable HMR ping functionality
    __VITE_HMR_PING_TIMEOUT__: 0,
    __VITE_HMR_DISABLE_PING__: true
  },
  server: {
    host: true, // Listen on all addresses
    port: 3000,
    strictPort: true,

    // Completely disable HMR to prevent any ping attempts
    hmr: false,

    // Direct configuration to allow any host
    cors: true,

    // Add explicit wildcard for all hosts
    allowedHosts: true,

    // Don't check origin at all
    origin: '*',

    // Enhanced proxy for API server to bypass CORS issues in all environments
    proxy: {
      '/api': {
        // For Docker environments, always use the container name
        // This is most reliable across all setups
        target: process.env.API_URL || 'http://gameserver:8080',
        changeOrigin: true,
        secure: false,
        ws: true,
        rewrite: (path) => path, // Don't rewrite paths
        
        // Enhanced logging and debugging for all environments
        configure: (proxy, options) => {
          console.log('Configuring proxy for API requests');
          console.log(`Proxy target: ${options.target}`);
          
          // Add request logging for all environments
          proxy.on('proxyReq', (proxyReq, req, res) => {
            // Preserve the original host from the request
            if (req.headers.host) {
              proxyReq.setHeader('host', req.headers.host);
            }
            
            // Add debugging headers that might help with container communication
            proxyReq.setHeader('x-forwarded-host', req.headers.host || '');
            proxyReq.setHeader('x-forwarded-proto', req.protocol || 'http');
            
            // Debug every request
            console.log(`Proxying request: ${req.method} ${req.url}`);
            console.log(`Original headers host: ${req.headers.host}`);
            console.log(`Target: ${options.target}`);
          });
          
          // Add response logging
          proxy.on('proxyRes', (proxyRes, req, res) => {
            console.log(`Proxy response: ${proxyRes.statusCode} for ${req.url}`);
          });
          
          // Add error logging
          proxy.on('error', (err, req, res) => {
            console.error(`Proxy error for ${req.url}:`, err);
          });
        }
      }
    },

    watch: {
      usePolling: true,
    },

    // Disable FS restriction
    fs: {
      strict: false,
      allow: ['..'],
    },
  },
})
