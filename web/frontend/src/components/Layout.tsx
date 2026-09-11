import { Header } from './Header'
import { meetingStationMode } from '@/lib/stationMode'

interface LayoutProps {
    children: React.ReactNode
}

export function Layout({ children }: LayoutProps) {
    if (meetingStationMode) return <div className="ms-app-layout"><Header /><main className="ms-shell-content">{children}</main></div>
    const handleFileSelect = () => {
        // Default behavior: do nothing or maybe navigate to home?
        // For now, consistent with Settings page behavior
    }

    return (
        <div className="min-h-screen">
            <div className="mx-auto w-full max-w-6xl px-3 sm:px-6 lg:px-8 pb-12 space-y-4 sm:space-y-8">
                <Header onFileSelect={handleFileSelect} />
                <main className="animate-fade-in">
                    {children}
                </main>
            </div>
        </div>
    )
}
