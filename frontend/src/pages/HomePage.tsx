export function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-slate-950 px-6 text-center text-slate-100">
      <p className="text-sm font-medium uppercase tracking-widest text-sky-400">ChangeGuard</p>
      <h1 className="text-3xl font-semibold">Project scaffold is running</h1>
      <p className="max-w-md text-sm text-slate-400">
        No business logic yet — this page confirms the frontend, backend, and Tailwind pipeline are
        wired together correctly.
      </p>
    </main>
  );
}
