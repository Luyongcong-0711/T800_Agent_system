import { notFound } from 'next/navigation'

import { WorkspaceShell } from '@/components/workspace/WorkspaceShell'
import { isWorkspaceSection, type SectionKey } from '@/components/workspace/routes'

interface WorkspaceSectionPageProps {
  params: Promise<{
    section: string
  }>
}

export default async function WorkspaceSectionPage({ params }: WorkspaceSectionPageProps) {
  const { section } = await params

  if (!isWorkspaceSection(section)) {
    notFound()
  }

  return <WorkspaceShell initialSection={section as SectionKey} />
}
