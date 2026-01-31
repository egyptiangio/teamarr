import { useState, useRef } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Plus, Trash2, Pencil, Loader2, Copy, Download, Upload } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
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
import { getTemplate, type Template } from "@/api/templates"

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
      let skipped = 0
      for (const template of imported) {
        // Validate required fields
        if (!template.name || !template.template_type) {
          skipped++
          continue
        }

        try {
          await createMutation.mutateAsync({
            name: template.name,
            template_type: template.template_type,
            sport: template.sport,
            league: template.league,
            title_format: template.title_format,
            subtitle_template: template.subtitle_template,
            description_template: template.description_template,
            program_art_url: template.program_art_url,
            game_duration_mode: template.game_duration_mode || "sport",
            game_duration_override: template.game_duration_override,
            xmltv_flags: template.xmltv_flags,
            xmltv_categories: template.xmltv_categories,
            categories_apply_to: template.categories_apply_to,
            pregame_enabled: template.pregame_enabled ?? true,
            pregame_fallback: template.pregame_fallback,
            postgame_enabled: template.postgame_enabled ?? true,
            postgame_fallback: template.postgame_fallback,
            postgame_conditional: template.postgame_conditional,
            idle_enabled: template.idle_enabled ?? false,
            idle_content: template.idle_content,
            idle_conditional: template.idle_conditional,
            idle_offseason: template.idle_offseason,
            conditional_descriptions: template.conditional_descriptions,
            event_channel_name: template.event_channel_name,
            event_channel_logo_url: template.event_channel_logo_url,
          })
          created++
        } catch {
          // Skip duplicates or invalid
        }
      }

      const message = skipped > 0
        ? `Imported ${created} templates (${skipped} skipped - missing name or template_type)`
        : `Imported ${created} templates`
      toast.success(message)
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
      // Fetch full template with all JSON fields (list endpoint only returns basic fields)
      const fullTemplate = await getTemplate(template.id)

      await createMutation.mutateAsync({
        name: `${fullTemplate.name} (copy)`,
        template_type: fullTemplate.template_type,
        sport: fullTemplate.sport,
        league: fullTemplate.league,
        title_format: fullTemplate.title_format,
        subtitle_template: fullTemplate.subtitle_template,
        description_template: fullTemplate.description_template,
        program_art_url: fullTemplate.program_art_url,
        game_duration_mode: fullTemplate.game_duration_mode || "sport",
        game_duration_override: fullTemplate.game_duration_override,
        xmltv_flags: fullTemplate.xmltv_flags,
        xmltv_categories: fullTemplate.xmltv_categories,
        categories_apply_to: fullTemplate.categories_apply_to,
        pregame_enabled: fullTemplate.pregame_enabled ?? true,
        pregame_fallback: fullTemplate.pregame_fallback,
        postgame_enabled: fullTemplate.postgame_enabled ?? true,
        postgame_fallback: fullTemplate.postgame_fallback,
        postgame_conditional: fullTemplate.postgame_conditional,
        idle_enabled: fullTemplate.idle_enabled ?? false,
        idle_content: fullTemplate.idle_content,
        idle_conditional: fullTemplate.idle_conditional,
        idle_offseason: fullTemplate.idle_offseason,
        conditional_descriptions: fullTemplate.conditional_descriptions,
        event_channel_name: fullTemplate.event_channel_name,
        event_channel_logo_url: fullTemplate.event_channel_logo_url,
      })
      toast.success(`Duplicated template "${template.name}"`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to duplicate template")
    }
  }

  const handleExportSingle = async (template: Template) => {
    try {
      // Fetch full template with all JSON fields (list endpoint only returns basic fields)
      const fullTemplate = await getTemplate(template.id)

      // Export without ID/timestamps (for portability)
      const {
        id,
        created_at,
        updated_at,
        team_count,
        group_count,
        ...exportData
      } = fullTemplate
      void id
      void created_at
      void updated_at
      void team_count
      void group_count
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
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to export template")
    }
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
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Templates</h1>
          <p className="text-sm text-muted-foreground">Configure EPG title and description templates</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={handleImportClick} disabled={isImporting}>
            {isImporting ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Upload className="h-4 w-4 mr-1" />
            )}
            Import
          </Button>
          <Button size="sm" onClick={() => navigate("/templates/new")}>
            <Plus className="h-4 w-4 mr-1" />
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

      <div className="border border-border rounded-lg overflow-hidden">
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
                  <TableHead className="w-[40%]">Name</TableHead>
                  <TableHead className="w-[80px]">Type</TableHead>
                  <TableHead className="w-[100px]">Usage</TableHead>
                  <TableHead className="w-[100px]">Created</TableHead>
                  <TableHead className="w-[160px] text-right">Actions</TableHead>
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
                          <Badge variant="outline" className="text-xs text-muted-foreground">
                            None
                          </Badge>
                        )
                      ) : (
                        template.group_count && template.group_count > 0 ? (
                          <Badge variant="outline" className="text-xs">
                            {template.group_count} group{template.group_count !== 1 ? "s" : ""}
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="text-xs text-muted-foreground">
                            None
                          </Badge>
                        )
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {template.created_at
                        ? new Date(template.created_at).toLocaleDateString("en-US", {
                            month: "short",
                            day: "numeric",
                            year: "numeric",
                          })
                        : "—"}
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
      </div>

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

          {/* Usage warning */}
          {deleteConfirm && (
            (deleteConfirm.template_type === "team" && deleteConfirm.team_count && deleteConfirm.team_count > 0) ||
            (deleteConfirm.template_type === "event" && deleteConfirm.group_count && deleteConfirm.group_count > 0)
          ) && (
            <div className="bg-destructive/10 border border-destructive/20 rounded-md p-3 text-sm">
              <p className="font-medium text-destructive">Warning</p>
              <p className="text-muted-foreground mt-1">
                {deleteConfirm.template_type === "team"
                  ? `${deleteConfirm.team_count} team${deleteConfirm.team_count !== 1 ? "s are" : " is"} currently using this template. They will become unassigned and won't generate EPG data until you assign them a new template.`
                  : `${deleteConfirm.group_count} event group${deleteConfirm.group_count !== 1 ? "s are" : " is"} currently using this template. They will become unassigned and won't generate EPG data until you assign them a new template.`
                }
              </p>
            </div>
          )}

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
