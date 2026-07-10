import React from 'react';

// Simple skeleton placeholder for list items or product cards
export default function SkeletonCard({ width = '100%', height = '150px' }) {
  return (
    <div
      style={{
        width,
        height,
        borderRadius: '8px',
        background: 'var(--skeleton-bg, #f0f0f0)',
        position: 'relative',
        overflow: 'hidden',
        marginBottom: '1rem',
      }}
      aria-busy="true"
      aria-label="loading"
    >
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundImage: 'linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.4) 50%, rgba(255,255,255,0) 100%)',
          animation: 'skeleton-shine 1.5s infinite',
        }}
      />
      <style>
        {`
          @keyframes skeleton-shine {
            0% { transform: translateX(-100%); }
            100% { transform: translateX(100%); }
          }
        `}
      </style>
    </div>
  );
}
