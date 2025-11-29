import { Injectable, NestMiddleware } from '@nestjs/common';
import { Request, Response, NextFunction } from 'express';
import { PrismaService } from '../../prisma/prisma.service';

@Injectable()
export class WorkspaceMembershipMiddleware implements NestMiddleware {
  constructor(private readonly prisma: PrismaService) {}

  async use(req: Request, res: Response, next: NextFunction) {
    const workspaceId = req.params['wid'] || req.params['workspaceId'];
    const user = (req as any).user;
    if (!workspaceId || !user) return next();
    const membership = await this.prisma.workspaceMember.findFirst({
      where: { workspaceId, userId: user.id },
    });
    if (!membership) {
      return res.status(403).json({ success: false, message: 'No workspace access' });
    }
    return next();
  }
}
