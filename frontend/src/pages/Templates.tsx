import { useState, useRef } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Plus, Trash2, Pencil, Loader2, Copy, Download, Upload } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import {
  useTemplates,
  useCreateTemplate,
  useDeleteTemplate,
} from "@/hooks/useTemplates"
import type { Template } from "@/api/templates"

export function Templates() {
  const navigate = useNavigate()
  const { data: templates, isLoading, error, refetch } = useTemplates()
  const createMutation = useCreateTemplate()
  const deleteMutation = useDeleteTemplate()

  const [deleteConfirm, setDeleteConfirm] = useState<Template | null>(null)
  const [isImporting, setIsImporting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleDelete = async () => {
    if (!deleteConfirm) return

    try {
      await deleteMutation.mutateAsync(deleteConfirm.id)
      toast.success(`Deleted template "${deleteConfirm.name}"`)
      setDeleteConfirm(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete template")
    }
  }

  const handleImportClick = () => {
    fileInputRef.current?.click()
  }

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    setIsImporting(true)
    try {
      const text = await file.text()
      const imported = JSON.parse(text)

      if (!Array.isArray(imported)) {
        throw new Error("Invalid format: expected an array of templates")
      }

      let created = 0
      for (const template of imported) {
        try {
          await createMutation.mutateAsync({
            name: template.name,
            template_type: template.template_type || "event",
            sport: template.sport,
            league: template.league,
            title_format: template.title_format,
            subtitle_template: template.subtitle_template,
            program_art_url: template.program_art_url,
            game_duration_mode: template.game_duration_mode || "sport",
            game_duration_override: template.game_duration_override,
            pregame_enabled: template.pregame_enabled ?? true,
            postgame_enabled: template.postgame_enabled ?? true,
            idle_enabled: template.idle_enabled ?? false,
            event_channel_name: template.event_channel_name,
            event_channel_logo_url: template.event_channel_logo_url,
          })
          created++
        } catch {
          // Skip duplicates or invalid
        }
      }

      toast.success(`Imported ${created} templates`)
      refetch()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to import templates")
    } finally {
      setIsImporting(false)
      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = ""
      }
    }
  }

  const handleDuplicate = async (template: Template) => {
    try {
      await createMutation.mutateAsync({
        name: `${template.name} (copy)`,
        template_type: template.template_type,
        sport: template.sport,
        league: template.league,
        title_format: template.title_format,
        subtitle_template: template.subtitle_template,
        program_art_url: template.program_art_url,
        game_duration_mode: template.game_duration_mode || "sport",
        game_duration_override: template.game_duration_override,
        pregame_enabled: template.pregame_enabled ?? true,
        postgame_enabled: template.postgame_enabled ?? true,
        idle_enabled: template.idle_enabled ?? false,
        event_channel_name: template.event_channel_name,
        event_channel_logo_url: template.event_channel_logo_url,
      })
      toast.success(`Duplicated template "${template.name}"`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to duplicate template")
    }
  }

  const handleExportSingle = (template: Template) => {
    // Export single template without ID (for portability)
    const { id, created_at, updated_at, ...exportData } = template
    const blob = new Blob([JSON.stringify([exportData], null, 2)], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `template-${template.name.toLowerCase().replace(/\s+/g, "-")}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    toast.success(`Exported template "${template.name}"`)
  }

  if (error) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">Templates</h1>
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive">Error loading templates: {error.message}</p>
            <Button className="mt-4" onClick={() => refetch()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Templates</h1>
          <p className="text-muted-foreground">Configure EPG title and description templates</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleImportClick} disabled={isImporting}>
            {isImporting ? (
              <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
            ) : (
              <Upload className="h-4 w-4 mr-1.5" />
            )}
            Import
          </Button>
          <Button onClick={() => navigate("/templates/new")}>
            <Plus className="h-4 w-4 mr-1.5" />
            New Template
          </Button>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".json"
          className="hidden"
          onChange={handleImportFile}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Templates ({templates?.length ?? 0})</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : templates?.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No templates configured. Create one to get started.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Usage</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {templates?.map((template) => (
                  <TableRow key={template.id}>
                    <TableCell className="font-medium">{template.name}</TableCell>
                    <TableCell>
                      <Badge variant={template.template_type === "team" ? "secondary" : "info"}>
                        {template.template_type}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {template.template_type === "team" ? (
                        template.team_count && template.team_count > 0 ? (
                          <Badge variant="outline" className="text-xs">
                            {template.team_count} team{template.team_count !== 1 ? "s" : ""}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground text-xs">None</span>
                        )
                      ) : (
                        template.group_count && template.group_count > 0 ? (
                          <Badge variant="outline" className="text-xs">
                            {template.group_count} group{template.group_count !== 1 ? "s" : ""}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground text-xs">None</span>
                        )
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => navigate(`/templates/${template.id}`)}
                          title="Edit"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => handleDuplicate(template)}
                          title="Duplicate"
                          disabled={createMutation.isPending}
                        >
                          <Copy className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => handleExportSingle(template)}
                          title="Export"
                        >
                          <Download className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => setDeleteConfirm(template)}
                          title="Delete"
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Delete Confirmation */}
      <Dialog
        open={deleteConfirm !== null}
        onOpenChange={(open) => !open && setDeleteConfirm(null)}
      >
        <DialogContent onClose={() => setDeleteConfirm(null)}>
          <DialogHeader>
            <DialogTitle>Delete Template</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{deleteConfirm?.name}"? This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteConfirm(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
