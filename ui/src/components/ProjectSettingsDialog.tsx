import type { Project } from "../api";
import { Modal } from "./Modal";
import { ProjectSettings } from "./ProjectSettings";
import { Revisions } from "./Revisions";

export function ProjectSettingsDialog(props: {
  project: Project;
  onSaved: (p: Project) => void;
  onClose: () => void;
  onError: (e: unknown) => void;
}) {
  return (
    <Modal title={`${props.project.name}: project settings`} onClose={props.onClose} wide>
      <p className="muted mono small">{props.project.root}</p>
      {props.project.notes.map((note) => (
        <div key={note} className="banner note">
          {note}
        </div>
      ))}
      <ProjectSettings project={props.project} onSaved={props.onSaved} onError={props.onError} />
      <Revisions revisions={props.project.revisions} />
    </Modal>
  );
}
