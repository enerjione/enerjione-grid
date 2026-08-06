"""Direk rol modeli: topology_role + energy_role.

Ekipman-tipi siniflandirmasi (pole_type) rol modeline donusturuldu. Sistem
ekipman envanteri degil: direk hattin NERESINDE (topolojik rol) ve enerji
akisinda NE IS goruyor (enerji rolu), onu tutar. pole_type kolonu geriye
uyum icin birakildi (yeni kod okumaz).

Geri doldurma kurallari (eski -> yeni):
  - transformer            -> energy_role=consumption   (direk tipi trafo)
  - source                 -> energy_role=generation    (fider cikisi)
  - branch_point           -> topology_role=branch
  - cable_transition       -> topology_role=cable_transition
  - breaker/disconnector/fuse_cutout -> ekipman bilgisi DUSER (transit/none)
  - bransman hedefi olan direkler    -> topology_role=branch
  - hattin ilk/son diregi            -> line_start / line_end (rol hala
    transit ise — acik rol atanmis diregi ezme)

Revision ID: 0045
Revises: 0044
"""

from alembic import op
import sqlalchemy as sa

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "poles",
        sa.Column("topology_role", sa.String(20), nullable=False, server_default="transit"),
    )
    op.add_column(
        "poles",
        sa.Column("energy_role", sa.String(20), nullable=False, server_default="none"),
    )

    # 1) Eski tiplerden dogrudan eslesenler.
    op.execute("UPDATE poles SET energy_role='consumption' WHERE pole_type='transformer'")
    op.execute("UPDATE poles SET energy_role='generation' WHERE pole_type='source'")
    op.execute("UPDATE poles SET topology_role='branch' WHERE pole_type='branch_point'")
    op.execute(
        "UPDATE poles SET topology_role='cable_transition' WHERE pole_type='cable_transition'"
    )

    # 2) Bransman hedefi olan direkler (bir hattin branched_from_pole_id'si
    #    bu direge isaret ediyor) -> branch. cable_transition ezilmez.
    op.execute(
        """
        UPDATE poles SET topology_role='branch'
        WHERE topology_role='transit'
          AND id IN (SELECT DISTINCT branched_from_pole_id FROM lines
                     WHERE branched_from_pole_id IS NOT NULL)
        """
    )

    # 3) Konumdan turetme: hattin ilk diregi line_start, son diregi line_end
    #    (yalnizca hala 'transit' olanlar — acik rol ezilmez).
    op.execute(
        """
        UPDATE poles SET topology_role='line_start'
        WHERE topology_role='transit'
          AND sequence_no = (SELECT MIN(p2.sequence_no) FROM poles p2
                             WHERE p2.line_id = poles.line_id)
        """
    )
    op.execute(
        """
        UPDATE poles SET topology_role='line_end'
        WHERE topology_role='transit'
          AND sequence_no = (SELECT MAX(p2.sequence_no) FROM poles p2
                             WHERE p2.line_id = poles.line_id)
        """
    )


def downgrade() -> None:
    op.drop_column("poles", "energy_role")
    op.drop_column("poles", "topology_role")
