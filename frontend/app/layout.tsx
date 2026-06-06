import { AntdRegistry } from '@ant-design/nextjs-registry'
import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import './globals.css'
import { AppProviders } from './providers'

export const metadata: Metadata = {
  title: 'Agent System',
  description: 'Agent System workspace console',
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AntdRegistry>
          <AppProviders>{children}</AppProviders>
        </AntdRegistry>
      </body>
    </html>
  )
}
